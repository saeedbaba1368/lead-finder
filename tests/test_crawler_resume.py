"""Phase 6.3.2.2.2B: resume-safe crawl state (BfsCrawler + PageRepository + CrawlRepository).

Loopback servers only (no internet). Restart tests use a file SQLite database with fresh engines and
sessions; one test kills a real subprocess mid-crawl.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import textwrap
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.crawler import CrawlPersistenceError
from app.db.base import Base
from app.db.session import create_db_engine
from app.models import CrawlStatus, CrawlStopReason, Page, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler, page  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML, HTML_HEADERS, server

ROOT = Path(__file__).resolve().parent.parent

SITE = {  # / -> a, b, c ; a -> a1
    "/": page("/a", "/b", "/c"),
    "/a": page("/a1"),
    "/b": page(),
    "/c": page(),
    "/a1": page(),
}


# ---------------------------------------------------------------- helpers
@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'resume.db'}"


@pytest.fixture
def file_db(db_url):
    import app.models  # noqa: F401

    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        domain, _ = DomainRepository(s).get_or_create("127.0.0.1")
        crawl = CrawlRepository(s).create(domain.id, seed_url="http://127.0.0.1")
        s.commit()
        crawl_id = crawl.id
    engine.dispose()
    return db_url, crawl_id


def run_crawl(url, crawl_id, base, *, seed=None, **kwargs):
    """One crawl run on its own engine/session (like a fresh process), committed at the end."""
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            async def main():
                async with make_crawler(
                    base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s), **kwargs
                ) as crawler:
                    return await crawler.crawl(seed or base + "/", crawl_id=crawl_id)

            result = asyncio.run(main())
            s.commit()
            return result
    finally:
        engine.dispose()


def snapshot(url, crawl_id):
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            pages = list(s.scalars(select(Page).where(Page.crawl_id == crawl_id).order_by(Page.id)))
            crawl = CrawlRepository(s).require(crawl_id)
            for p in pages:
                s.expunge(p)
            s.expunge(crawl)
            return pages, crawl
    finally:
        engine.dispose()


def paths(state, base):
    return [r.removeprefix(base) for r in state.requests]


def by_status(pages, base):
    out: dict[PageStatus, list[str]] = {}
    for p in pages:
        out.setdefault(p.status, []).append(p.url.removeprefix(base))
    return out


# ---------------------------------------------------------------- 1. fresh crawl creates state
def test_fresh_crawl_creates_persistence_state(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        result = run_crawl(url, cid, base)
        pages, crawl = snapshot(url, cid)
    assert len(result.pages) == 5
    assert sorted(p.url.removeprefix(base) for p in pages) == ["/", "/a", "/a1", "/b", "/c"]
    assert all(p.status is PageStatus.FETCHED for p in pages)
    assert crawl.status is CrawlStatus.COMPLETED and crawl.stop_reason is CrawlStopReason.COMPLETED
    assert crawl.pages_crawled == 5 and crawl.started_at and crawl.finished_at
    assert crawl.elapsed_seconds > 0 and crawl.last_checkpoint_at is not None
    assert (crawl.max_pages, crawl.max_depth, crawl.request_delay, crawl.max_crawl_time) == (100, 3, 0.0, None)
    parents = {p.url.removeprefix(base): (p.parent_url or "").removeprefix(base) for p in pages}
    assert parents == {"/": "", "/a": "/", "/b": "/", "/c": "/", "/a1": "/a"}


# ---------------------------------------------------------------- 2/3. second crawl loads state, no re-fetch
def test_second_crawl_loads_visited_state_and_fetches_nothing(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        run_crawl(url, cid, base)
        assert len(state.requests) == 5
        second = run_crawl(url, cid, base)
        assert len(state.requests) == 5  # no duplicate HTTP requests on resume
        assert sorted(Counter(state.requests).values()) == [1] * 5
    assert second.pages == []
    assert second.queued and len(second.queued) == 5
    assert second.stop_reason.value == "completed"


# ---------------------------------------------------------------- 4. duplicate discovered URLs
def test_duplicate_discovered_urls_are_fetched_once(local_network, file_db):
    url, cid = file_db
    routes = {
        "/": page("/a", "/a/", "/a#frag", "/a?utm_source=x", "./a", "b", "/b"),
        "/a": page("/b", "/"),
        "/b": page("/a"),
    }
    with server(routes) as (base, state):
        run_crawl(url, cid, base)
        run_crawl(url, cid, base)
        assert sorted(paths(state, base)) == ["/", "/a", "/b"]
        pages, _ = snapshot(url, cid)
    assert len(pages) == 3


# ---------------------------------------------------------------- 5. normalized-equivalent duplicates
def test_normalized_equivalent_urls_create_one_visited_entry(local_network, file_db):
    url, cid = file_db
    routes = {"/": page("/a", "/a/", "/a#x", "./a", "/a?utm_source=n"), "/a": page()}
    with server(routes) as (base, _):
        run_crawl(url, cid, base)
        run_crawl(url, cid, base)
        pages, _ = snapshot(url, cid)
    assert sorted(p.url.removeprefix(base) for p in pages) == ["/", "/a"]
    assert len({p.url_hash for p in pages}) == 2


# ---------------------------------------------------------------- 6. metadata persisted and kept
def test_page_metadata_is_persisted_and_survives_resume(local_network, file_db):
    url, cid = file_db
    routes = {"/": page("/old"), "/old": (301, b"", {"Location": "/new"}), "/new": (200, HTML, HTML_HEADERS)}
    with server(routes) as (base, _):
        run_crawl(url, cid, base)
        before, _ = snapshot(url, cid)
        run_crawl(url, cid, base)
        after, _ = snapshot(url, cid)
    row = next(p for p in after if p.url.endswith("/old"))
    assert row.final_url == base + "/new" and row.redirect_count == 1
    assert row.charset == "utf-8" and row.response_size == len(HTML) and row.duration_seconds >= 0
    assert row.dedupe_key == Page.hash_url(base + "/new")
    assert [(p.id, p.final_url, p.dedupe_key) for p in before] == [(p.id, p.final_url, p.dedupe_key) for p in after]


# ---------------------------------------------------------------- 7. idempotence
def test_repeated_runs_are_idempotent(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        run_crawl(url, cid, base)
        first, crawl1 = snapshot(url, cid)
        for _ in range(3):
            run_crawl(url, cid, base)
        again, crawl2 = snapshot(url, cid)
        assert len(state.requests) == 5
    assert [(p.id, p.url, p.status) for p in again] == [(p.id, p.url, p.status) for p in first]
    assert crawl2.status is CrawlStatus.COMPLETED and crawl2.pages_crawled == crawl1.pages_crawled == 5


# ---------------------------------------------------------------- 8. max_pages interruption / resume
def test_max_pages_interruption_and_resume(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        first = run_crawl(url, cid, base, max_pages=2)
        assert first.stop_reason.value == "max_pages"
        pages, crawl = snapshot(url, cid)
        assert by_status(pages, base) == {PageStatus.FETCHED: ["/", "/a"], PageStatus.SKIPPED: ["/b", "/c", "/a1"]}
        assert crawl.status is CrawlStatus.RUNNING and crawl.stop_reason is CrawlStopReason.MAX_PAGES

        same = run_crawl(url, cid, base, max_pages=2)  # same limit: nothing more to do, nothing requested
        assert same.pages == [] and same.stop_reason.value == "max_pages"
        assert paths(state, base) == ["/", "/a"]

        rest = run_crawl(url, cid, base, max_pages=10)
        assert rest.stop_reason.value == "completed"
        assert sorted(paths(state, base)) == ["/", "/a", "/a1", "/b", "/c"]  # each URL exactly once
        pages, crawl = snapshot(url, cid)
    assert all(p.status is PageStatus.FETCHED for p in pages) and len(pages) == 5
    assert crawl.status is CrawlStatus.COMPLETED and crawl.pages_crawled == 5


# ---------------------------------------------------------------- 9. max_crawl_time interruption / resume
def test_max_crawl_time_interruption_and_resume(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        first = run_crawl(url, cid, base, request_delay=0.3, max_crawl_time=0.5)
        assert first.stop_reason.value == "max_crawl_time" and len(first.pages) == 2
        pages, crawl = snapshot(url, cid)
        assert crawl.status is CrawlStatus.RUNNING and crawl.stop_reason is CrawlStopReason.MAX_CRAWL_TIME
        assert crawl.max_crawl_time == 0.5 and crawl.request_delay == 0.3 and crawl.elapsed_seconds > 0
        done = {p.url.removeprefix(base) for p in pages if p.status is PageStatus.FETCHED}
        assert len(done) == 2 and any(p.status is PageStatus.PENDING for p in pages)
        elapsed_before = crawl.elapsed_seconds

        rest = run_crawl(url, cid, base)  # fresh time budget per run
        assert rest.stop_reason.value == "completed"
        assert sorted(paths(state, base)) == ["/", "/a", "/a1", "/b", "/c"]
        _, crawl = snapshot(url, cid)
    assert crawl.status is CrawlStatus.COMPLETED and crawl.elapsed_seconds > elapsed_before


# ---------------------------------------------------------------- 10. restart in a separate process
KILL_SCRIPT = textwrap.dedent(
    """
    import asyncio, os, sys
    from sqlalchemy.orm import Session
    from app.db.session import create_db_engine
    from app.repositories import CrawlRepository, PageRepository
    from tests.test_crawler_bfs import make_crawler

    base, url, crawl_id, die_after = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])

    async def main():
        engine = create_db_engine(url)
        with Session(engine) as s:
            crawler = make_crawler(base, page_repository=PageRepository(s),
                                   crawl_repository=CrawlRepository(s), commit_checkpoints=True)
            real, calls = crawler._client.fetch, 0

            async def fetch(u):
                nonlocal calls
                calls += 1
                if calls > die_after:
                    os._exit(3)  # hard kill: no cleanup, no final commit
                return await real(u)

            crawler._client.fetch = fetch
            await crawler.crawl(base + "/", crawl_id=crawl_id)

    asyncio.run(main())
    """
)


def test_resume_after_the_process_was_killed(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        proc = subprocess.run(
            [sys.executable, "-c", KILL_SCRIPT, base, url, str(cid), "2"],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 3, proc.stderr
        assert paths(state, base) == ["/", "/a"]  # the third request never happened
        pages, crawl = snapshot(url, cid)
        states = by_status(pages, base)
        assert states[PageStatus.FETCHED] == ["/", "/a"]  # the killed request is NOT recorded as visited
        assert sorted(states[PageStatus.PENDING]) == ["/a1", "/b", "/c"]
        assert crawl.status is CrawlStatus.RUNNING

        resumed = run_crawl(url, cid, base)  # new process / engine / session
        assert resumed.stop_reason.value == "completed"
        assert sorted(paths(state, base)) == ["/", "/a", "/a1", "/b", "/c"]  # no URL requested twice
        pages, crawl = snapshot(url, cid)
    assert len(pages) == 5 and all(p.status is PageStatus.FETCHED for p in pages)
    assert crawl.status is CrawlStatus.COMPLETED


# ---------------------------------------------------------------- 11. persistence failure
def test_persistence_failure_raises_clear_error_and_leaves_no_false_state(
    local_network, file_db, monkeypatch, caplog
):
    url, cid = file_db
    real = PageRepository.mark_fetched
    calls = {"n": 0}

    def flaky(self, page_row, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OperationalError("UPDATE pages", {}, Exception("disk I/O error"))
        return real(self, page_row, **kw)

    monkeypatch.setattr(PageRepository, "mark_fetched", flaky)
    with server(SITE) as (base, state):
        with caplog.at_level(logging.ERROR):
            with pytest.raises(CrawlPersistenceError, match="could not persist page") as info:
                run_crawl(url, cid, base, commit_checkpoints=True)
        assert any(r.message == "crawl_persistence_failed" for r in caplog.records)
        assert isinstance(info.value.__cause__, OperationalError)
        assert info.value.result is not None and len(info.value.result.pages) == 3
        pages, _ = snapshot(url, cid)
        st = by_status(pages, base)
        assert len(st[PageStatus.FETCHED]) == 2  # the failed write is not recorded as visited
        failed_path = next(p for p in paths(state, base) if p not in st[PageStatus.FETCHED])
        assert failed_path in st[PageStatus.PENDING]  # still queued, so it will be retried

        monkeypatch.setattr(PageRepository, "mark_fetched", real)
        run_crawl(url, cid, base)
        pages, _ = snapshot(url, cid)
    assert len(pages) == 5 and all(p.status is PageStatus.FETCHED for p in pages)
    assert Counter(paths(state, base))[failed_path] == 2  # only the URL whose write failed is re-requested
    assert sum(1 for v in Counter(paths(state, base)).values() if v > 1) == 1


# ---------------------------------------------------------------- 12. new / empty store, persistence disabled
def test_empty_store_and_persistence_disabled_work(local_network, file_db):
    url, cid = file_db
    pages, crawl = snapshot(url, cid)
    assert pages == [] and crawl.status is CrawlStatus.PENDING and crawl.stop_reason is None
    with server(SITE) as (base, state):
        async def plain():  # no repository at all: behaviour of earlier phases
            async with make_crawler(base) as crawler:
                return await crawler.crawl(base + "/")

        result = asyncio.run(plain())
        assert len(result.pages) == 5 and len(state.requests) == 5
        # only a page repository (no crawl repository) is also supported
        engine = create_db_engine(url)
        with Session(engine) as s:
            async def pages_only():
                async with make_crawler(base, page_repository=PageRepository(s)) as crawler:
                    return await crawler.crawl(base + "/", crawl_id=cid)

            asyncio.run(pages_only())
            assert PageRepository(s).count_for_crawl(cid) == 5
        engine.dispose()


# ---------------------------------------------------------------- 13. existing store with data
def test_existing_store_with_data_is_respected(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        engine = create_db_engine(url)
        with Session(engine) as s:  # a store prepared earlier: "/" fetched, "/a" and "/b" queued
            repo = PageRepository(s)
            root, _ = repo.get_or_create(cid, base + "/")
            repo.mark_fetched(root, http_status=200, content_type="text/html")
            repo.get_or_create(cid, base + "/a", depth=1, parent_url=base + "/")
            repo.get_or_create(cid, base + "/b", depth=1, parent_url=base + "/")
            s.commit()
        engine.dispose()
        result = run_crawl(url, cid, base)
        assert paths(state, base) == ["/a", "/b", "/a1"]  # "/" is never requested again
        pages, _ = snapshot(url, cid)
    assert result.stop_reason.value == "completed"
    assert sorted(p.url.removeprefix(base) for p in pages) == ["/", "/a", "/a1", "/b"]


# ---------------------------------------------------------------- 14. queue / visited consistency
def test_queue_and_visited_are_consistent_after_resume(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        run_crawl(url, cid, base, max_pages=3)  # /, /a, /b fetched; /c and /a1 skipped
        resumed = run_crawl(url, cid, base, max_pages=3)
        assert resumed.pages == [] and len(resumed.queued) == len(set(resumed.queued)) == 3
        assert resumed.pending == []  # every queued URL was visited
        final = run_crawl(url, cid, base, max_pages=10)
        pages, _ = snapshot(url, cid)
    all_urls = [p.url for p in pages]
    assert len(all_urls) == len(set(all_urls))  # no logical URL twice
    assert len({p.url_hash for p in pages}) == len(pages)
    assert set(final.queued) == set(all_urls) and len(final.queued) == len(all_urls)
    assert final.pending == []
    assert sorted(paths(state, base)) == ["/", "/a", "/a1", "/b", "/c"]
    assert session_count(url, cid) == 5


def session_count(url, cid):
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            return s.scalar(select(func.count()).select_from(Page).where(Page.crawl_id == cid))
    finally:
        engine.dispose()
