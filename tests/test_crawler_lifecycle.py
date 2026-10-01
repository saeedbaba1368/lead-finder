"""Phase 6.4: crawl lifecycle, graceful cancellation/shutdown, resume after cancellation.

Loopback servers and fakes only (the conftest blocks every other socket). Cancellation is triggered
deterministically from inside the crawler (hooks) or with short timers; no sleeps decide an outcome.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.crawler.bfs as bfs_module
from app.crawler import (
    CrawlLifecycle,
    CrawlPersistenceError,
    CrawlTerminalError,
    StopReason,
    inspect_crawl_state,
    is_resumable,
    lifecycle_of,
)
from app.db.base import Base
from app.db.session import create_db_engine
from app.models import CrawlStatus, CrawlStopReason, Page, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler, page  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML, HTML_HEADERS, server

SITE = {  # / -> a, b, c ; a -> a1   (5 pages)
    "/": page("/a", "/b", "/c"),
    "/a": page("/a1"),
    "/b": page(),
    "/c": page(),
    "/a1": page(),
}


# ---------------------------------------------------------------- helpers
def new_crawl(session):
    domain, _ = DomainRepository(session).get_or_create("127.0.0.1")
    return CrawlRepository(session).create(domain.id, seed_url="http://127.0.0.1")


def go(session, base, cid, *, setup=None, holder=None, **kwargs):
    """One crawl() run with a new crawler; `holder['crawler']` is set so tests can inspect it afterwards."""
    holder = holder if holder is not None else {}

    async def main():
        async with make_crawler(
            base, page_repository=PageRepository(session), crawl_repository=CrawlRepository(session), **kwargs
        ) as crawler:
            holder["crawler"] = crawler
            if setup is not None:
                setup(crawler)
            return await crawler.crawl(base + "/", crawl_id=cid)

    return asyncio.run(main())


def cancel_after(n):
    """Call crawler.cancel() right after the n-th page was persisted."""
    def setup(crawler):
        real, seen = crawler._persist_page, {"n": 0}

        def wrapper(*args, **kwargs):
            real(*args, **kwargs)
            seen["n"] += 1
            if seen["n"] == n:
                crawler.cancel()

        crawler._persist_page = wrapper
    return setup


def paths(state, base):
    return [r.removeprefix(base) for r in state.requests]


def rows(session, cid):
    return list(session.scalars(select(Page).where(Page.crawl_id == cid).order_by(Page.id)))


def statuses(session, cid, base):
    out: dict[PageStatus, list[str]] = {}
    for p in rows(session, cid):
        out.setdefault(p.status, []).append(p.url.removeprefix(base))
    return out


def crawl_row(session, cid):
    session.expire_all()
    return CrawlRepository(session).require(cid)


# ---------------------------------------------------------------- lifecycle mapping (pure)
def test_lifecycle_mapping():
    S, R = CrawlStatus, CrawlStopReason
    assert lifecycle_of(S.PENDING, None) is CrawlLifecycle.CREATED
    assert lifecycle_of(S.RUNNING, None) is CrawlLifecycle.RUNNING
    for reason in (R.CANCELLED, R.MAX_PAGES, R.MAX_CRAWL_TIME):
        assert lifecycle_of(S.RUNNING, reason) is CrawlLifecycle.STOPPED
    assert lifecycle_of(S.COMPLETED, R.COMPLETED) is CrawlLifecycle.COMPLETED
    assert lifecycle_of(S.FAILED, None) is CrawlLifecycle.FAILED
    assert lifecycle_of(S.CANCELLED, None) is CrawlLifecycle.STOPPED
    assert is_resumable(S.RUNNING) and is_resumable(S.PENDING)
    assert not any(is_resumable(s) for s in (S.COMPLETED, S.FAILED, S.CANCELLED))
    assert {m.value for m in CrawlLifecycle} == {"created", "running", "stopping", "completed", "stopped", "failed"}


# ---------------------------------------------------------------- 1. normal completion
def test_normal_completion(local_network, session):
    crawl = new_crawl(session)
    holder = {}
    with server(SITE) as (base, state):
        result = go(session, base, crawl.id, holder=holder)
    assert result.stop_reason is StopReason.COMPLETED and len(result.pages) == 5
    assert holder["crawler"].lifecycle is CrawlLifecycle.COMPLETED
    row = crawl_row(session, crawl.id)
    assert row.status is CrawlStatus.COMPLETED and row.stop_reason is CrawlStopReason.COMPLETED
    assert inspect_crawl_state(session, crawl.id).lifecycle is CrawlLifecycle.COMPLETED


def test_crawler_starts_in_created_state(session):
    assert make_crawler("http://127.0.0.1").lifecycle is CrawlLifecycle.CREATED


# ---------------------------------------------------------------- 2. empty queue
def test_empty_queue_completes(local_network, session):
    crawl = new_crawl(session)
    with server({"/": page()}) as (base, state):
        result = go(session, base, crawl.id)
        assert paths(state, base) == ["/"]
    assert result.stop_reason is StopReason.COMPLETED and result.pending == []
    assert crawl_row(session, crawl.id).status is CrawlStatus.COMPLETED


# ---------------------------------------------------------------- 3. max_pages
def test_max_pages_stops_resumably(local_network, session):
    crawl = new_crawl(session)
    holder = {}
    with server(SITE) as (base, state):
        result = go(session, base, crawl.id, max_pages=2, holder=holder)
    assert result.stop_reason is StopReason.MAX_PAGES
    assert holder["crawler"].lifecycle is CrawlLifecycle.STOPPED
    row = crawl_row(session, crawl.id)
    assert row.status is CrawlStatus.RUNNING and row.stop_reason is CrawlStopReason.MAX_PAGES
    report = inspect_crawl_state(session, crawl.id)
    assert report.lifecycle is CrawlLifecycle.STOPPED and report.visited == 2 and report.skipped == 3


# ---------------------------------------------------------------- 4. max_crawl_time
def test_max_crawl_time_stops_and_persists(local_network, session):
    crawl = new_crawl(session)
    holder = {}
    with server(SITE) as (base, state):
        result = go(session, base, crawl.id, request_delay=0.3, max_crawl_time=0.5, holder=holder)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME and len(result.pages) == 2
    assert holder["crawler"].lifecycle is CrawlLifecycle.STOPPED
    row = crawl_row(session, crawl.id)
    assert row.status is CrawlStatus.RUNNING and row.stop_reason is CrawlStopReason.MAX_CRAWL_TIME
    assert len(statuses(session, crawl.id, base)[PageStatus.FETCHED]) == 2


# ---------------------------------------------------------------- 5. manual cancellation
def test_manual_cancellation(local_network, session):
    crawl = new_crawl(session)
    holder = {}
    with server(SITE) as (base, state):
        result = go(session, base, crawl.id, setup=cancel_after(2), holder=holder)
        assert len(state.requests) == 2  # nothing new was started after cancel()
    assert result.stop_reason is StopReason.CANCELLED and len(result.pages) == 2
    assert holder["crawler"].lifecycle is CrawlLifecycle.STOPPED
    row = crawl_row(session, crawl.id)
    assert row.status is CrawlStatus.RUNNING and row.stop_reason is CrawlStopReason.CANCELLED
    assert row.finished_at is None and row.pages_crawled == 2  # stopped, not finished
    report = inspect_crawl_state(session, crawl.id)
    assert report.lifecycle is CrawlLifecycle.STOPPED and report.stop_reason == "cancelled"


# ---------------------------------------------------------------- 6. cancellation during async operations
def test_cancel_while_waiting_for_the_request_delay(local_network, session):
    crawl = new_crawl(session)
    holder = {}

    async def main(base):
        async with make_crawler(
            base, page_repository=PageRepository(session), crawl_repository=CrawlRepository(session),
            request_delay=30.0,
        ) as crawler:
            holder["crawler"] = crawler
            asyncio.get_running_loop().call_later(0.3, crawler.cancel)  # while the 2nd request waits
            started = asyncio.get_running_loop().time()
            result = await crawler.crawl(base + "/", crawl_id=crawl.id)
            leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return result, asyncio.get_running_loop().time() - started, leftovers

    with server(SITE) as (base, state):
        result, took, leftovers = asyncio.run(main(base))
        assert paths(state, base) == ["/"]
    assert took < 5 and leftovers == []  # the 30 s wait was interrupted, no helper task left behind
    assert result.stop_reason is StopReason.CANCELLED and len(result.pages) == 1
    assert crawl_row(session, crawl.id).stop_reason is CrawlStopReason.CANCELLED
    assert statuses(session, crawl.id, base)[PageStatus.PENDING] == ["/a", "/b", "/c"]


def test_cancel_while_a_request_is_in_flight_finishes_that_page(local_network, session):
    crawl = new_crawl(session)
    routes = {"/": page("/slow", "/b"), "/slow": (200, HTML, HTML_HEADERS), "/b": page()}

    def setup(crawler):
        asyncio.get_running_loop().call_later(0.4, crawler.cancel)  # /slow takes 1.5 s on the server

    with server(routes) as (base, state):
        result = go(session, base, crawl.id, setup=setup)
        assert paths(state, base) == ["/", "/slow"]
    assert result.stop_reason is StopReason.CANCELLED
    st = statuses(session, crawl.id, base)
    assert st[PageStatus.FETCHED] == ["/", "/slow"]  # the in-flight page was completed and stored
    assert st[PageStatus.PENDING] == ["/b"]


@pytest.mark.parametrize("step", ["extract_links", "record_discovered", "store_page"])
def test_cancel_during_processing_steps_keeps_state_consistent(local_network, session, monkeypatch, step):
    """cancel() arrives while the first page is being processed: link discovery, queueing, persisting."""
    crawl = new_crawl(session)
    state_box = {}

    def setup(crawler):
        state_box["crawler"] = crawler
        fired = {"done": False}

        def fire():
            if not fired["done"]:
                fired["done"] = True
                crawler.cancel()

        if step == "extract_links":
            real = bfs_module.extract_links
            monkeypatch.setattr(bfs_module, "extract_links", lambda *a, **k: (fire(), real(*a, **k))[1])
        elif step == "record_discovered":
            real = crawler._record_discovered

            def rec(url, depth, parent, **kw):
                if parent is not None:
                    fire()
                return real(url, depth, parent, **kw)

            crawler._record_discovered = rec
        else:
            real = crawler._store_page
            crawler._store_page = lambda *a, **k: (fire(), real(*a, **k))[1]

    with server(SITE) as (base, state):
        result = go(session, base, crawl.id, setup=setup)
        assert paths(state, base) == ["/"]
        assert result.stop_reason is StopReason.CANCELLED
        st = statuses(session, crawl.id, base)
        assert st[PageStatus.FETCHED] == ["/"]  # the page being processed was finished and stored
        assert sorted(st[PageStatus.PENDING]) == ["/a", "/b", "/c"]  # and all its links were queued
        row = crawl_row(session, crawl.id)
        assert row.stop_reason is CrawlStopReason.CANCELLED and row.status is CrawlStatus.RUNNING

        go(session, base, crawl.id)  # a new crawler resumes
        assert Counter(paths(state, base)) == Counter(["/", "/a", "/b", "/c", "/a1"])
    assert crawl_row(session, crawl.id).status is CrawlStatus.COMPLETED


def test_task_cancellation_saves_state_and_propagates(local_network, session):
    """The asyncio task itself is cancelled while a request is hanging."""
    crawl = new_crawl(session)
    holder = {}

    async def main(base):
        async with make_crawler(
            base, page_repository=PageRepository(session), crawl_repository=CrawlRepository(session)
        ) as crawler:
            holder["crawler"] = crawler
            real, calls = crawler._client.fetch, {"n": 0}

            async def fetch(url):
                calls["n"] += 1
                if calls["n"] == 3:
                    await asyncio.Event().wait()  # never answers
                return await real(url)

            crawler._client.fetch = fetch
            task = asyncio.create_task(crawler.crawl(base + "/", crawl_id=crawl.id))
            while calls["n"] < 3:
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    with server(SITE) as (base, state):
        leftovers = asyncio.run(main(base))
        assert leftovers == []
        assert holder["crawler"].lifecycle is CrawlLifecycle.STOPPED
        assert holder["crawler"]._client._client.is_closed  # shutdown still released the HTTP client
        row = crawl_row(session, crawl.id)
        assert row.status is CrawlStatus.RUNNING and row.stop_reason is CrawlStopReason.CANCELLED
        st = statuses(session, crawl.id, base)
        assert len(st[PageStatus.FETCHED]) == 2 and len(st[PageStatus.PENDING]) == 3  # the hung URL is pending
        go(session, base, crawl.id)
        assert Counter(paths(state, base)) == Counter(["/", "/a", "/b", "/c", "/a1"])  # nothing twice
    assert crawl_row(session, crawl.id).status is CrawlStatus.COMPLETED


# ---------------------------------------------------------------- 7/8. persisted state and resume after cancellation
def test_resume_after_cancellation(local_network, session):
    crawl = new_crawl(session)
    with server(SITE) as (base, state):
        go(session, base, crawl.id, setup=cancel_after(2))
        before = {p.url: p.id for p in rows(session, crawl.id)}
        assert paths(state, base) == ["/", "/a"]
        st = statuses(session, crawl.id, base)
        assert st[PageStatus.FETCHED] == ["/", "/a"] and sorted(st[PageStatus.PENDING]) == ["/a1", "/b", "/c"]

        holder = {}
        result = go(session, base, crawl.id, holder=holder)  # new crawler, same state
        assert result.stop_reason is StopReason.COMPLETED and len(result.pages) == 3
        assert result.previously_visited and len(result.previously_visited) == 2
        assert Counter(paths(state, base)) == Counter(["/", "/a", "/a1", "/b", "/c"])  # each exactly once
    after = {p.url: p.id for p in rows(session, crawl.id)}
    assert set(before) <= set(after) and all(after[u] == i for u, i in before.items())  # same rows reused
    assert len(after) == 5 and holder["crawler"].lifecycle is CrawlLifecycle.COMPLETED
    row = crawl_row(session, crawl.id)
    assert row.status is CrawlStatus.COMPLETED and row.stop_reason is CrawlStopReason.COMPLETED


# ---------------------------------------------------------------- 9/10. cleanup
def test_http_client_is_closed_after_every_ending(local_network, session):
    with server(SITE) as (base, state):
        for how in ("complete", "cancel", "fatal"):
            crawl = new_crawl(session)
            holder = {}
            if how == "fatal":
                def boom(crawler):
                    async def fetch(url):
                        raise RuntimeError("boom")
                    crawler._client.fetch = fetch

                with pytest.raises(RuntimeError):
                    go(session, base, crawl.id, setup=boom, holder=holder)
            else:
                go(session, base, crawl.id, setup=cancel_after(1) if how == "cancel" else None, holder=holder)
            assert holder["crawler"]._client._client.is_closed, how


def test_persistence_is_left_consistent_after_cancel_and_failure(local_network, tmp_path, monkeypatch):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'c.db'}")
    Base.metadata.create_all(engine)
    with server(SITE) as (base, state):
        with Session(engine) as s:
            crawl = new_crawl(s)
            s.commit()
            cid = crawl.id
            go(s, base, cid, setup=cancel_after(2), commit_checkpoints=True)
            assert not s.in_nested_transaction() and not s.in_transaction()  # committed, nothing left open
        with Session(engine) as fresh:  # what another process would see
            assert crawl_row(fresh, cid).stop_reason is CrawlStopReason.CANCELLED
            assert len(statuses(fresh, cid, base)[PageStatus.FETCHED]) == 2

        real = PageRepository.mark_fetched
        n = {"calls": 0}

        def flaky(self, row, **kw):
            n["calls"] += 1
            if n["calls"] == 1:
                raise OperationalError("UPDATE pages", {}, Exception("disk I/O error"))
            return real(self, row, **kw)

        monkeypatch.setattr(PageRepository, "mark_fetched", flaky)
        with Session(engine) as s:
            with pytest.raises(CrawlPersistenceError):
                go(s, base, cid, commit_checkpoints=True)
            assert not s.in_nested_transaction() and not s.new and not s.dirty
    engine.dispose()


# ---------------------------------------------------------------- 11. fatal error -> FAILED
def test_fatal_error_marks_the_crawl_failed(local_network, session, caplog):
    crawl = new_crawl(session)
    holder = {}

    def setup(crawler):
        real, calls = crawler._client.fetch, {"n": 0}

        async def fetch(url):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return await real(url)

        crawler._client.fetch = fetch

    with server(SITE) as (base, state):
        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="boom"):  # never converted into a "completed" result
                go(session, base, crawl.id, setup=setup, holder=holder)
        assert any(r.message == "crawl_failed" for r in caplog.records)
        assert holder["crawler"].lifecycle is CrawlLifecycle.FAILED
        row = crawl_row(session, crawl.id)
        assert row.status is CrawlStatus.FAILED and row.error_message == "RuntimeError: boom"
        assert row.finished_at is not None
        assert inspect_crawl_state(session, crawl.id).lifecycle is CrawlLifecycle.FAILED
        assert statuses(session, crawl.id, base)[PageStatus.FETCHED] == ["/"]  # earlier work is kept
        with pytest.raises(CrawlTerminalError):  # and a failed crawl is not silently restarted
            go(session, base, crawl.id)
        assert paths(state, base) == ["/"]


# ---------------------------------------------------------------- 12. persistence failure is not success
def test_persistence_failure_is_not_reported_as_completion(local_network, session, monkeypatch):
    crawl = new_crawl(session)
    real = PageRepository.mark_fetched
    n = {"calls": 0}

    def flaky(self, row, **kw):
        n["calls"] += 1
        if n["calls"] == 3:
            raise OperationalError("UPDATE pages", {}, Exception("disk I/O error"))
        return real(self, row, **kw)

    monkeypatch.setattr(PageRepository, "mark_fetched", flaky)
    holder = {}
    with server(SITE) as (base, state):
        with pytest.raises(CrawlPersistenceError) as info:
            go(session, base, crawl.id, holder=holder)
        assert holder["crawler"].lifecycle is CrawlLifecycle.FAILED
        assert info.value.result is not None and info.value.result.stop_reason is StopReason.COMPLETED
        row = crawl_row(session, crawl.id)
        assert row.status is CrawlStatus.RUNNING and row.status is not CrawlStatus.COMPLETED
        assert row.stop_reason is None  # no "finished" claim was written
        assert len(statuses(session, crawl.id, base)[PageStatus.FETCHED]) == 2

        monkeypatch.setattr(PageRepository, "mark_fetched", real)  # the store works again: it resumes
        go(session, base, crawl.id)
        assert crawl_row(session, crawl.id).status is CrawlStatus.COMPLETED
        counts = Counter(paths(state, base))
        assert sum(1 for v in counts.values() if v > 1) == 1 and len(counts) == 5  # only the unsaved URL again


# ---------------------------------------------------------------- 13. repeated cancellation
def test_repeated_cancellation_is_safe(local_network, session):
    crawl = new_crawl(session)
    idle = make_crawler("http://127.0.0.1")
    for _ in range(3):
        idle.cancel()  # before any crawl: harmless
    with server(SITE) as (base, state):
        def setup(crawler):
            real = crawler._persist_page

            def wrapper(*a, **k):
                real(*a, **k)
                for _ in range(3):  # repeated cancellation during the run
                    crawler.cancel()

            crawler._persist_page = wrapper

        holder = {}
        result = go(session, base, crawl.id, setup=setup, holder=holder)
        holder["crawler"].cancel()  # and after it ended
        assert result.stop_reason is StopReason.CANCELLED and len(result.pages) == 1
        assert holder["crawler"].lifecycle is CrawlLifecycle.STOPPED

        # a cancel() issued before crawl() applies to that crawl only
        pre = {}
        def pre_cancel(crawler):
            crawler.cancel()
            crawler.cancel()

        stopped = go(session, base, crawl.id, setup=pre_cancel, holder=pre)
        assert stopped.stop_reason is StopReason.CANCELLED and stopped.pages == []
        assert paths(state, base) == ["/"]
        done = go(session, base, crawl.id)
        assert done.stop_reason is StopReason.COMPLETED
    assert crawl_row(session, crawl.id).status is CrawlStatus.COMPLETED


# ---------------------------------------------------------------- 14. terminal crawls are not restarted
def test_terminal_crawls_are_not_restarted(local_network, session):
    with server(SITE) as (base, state):
        done = new_crawl(session)
        go(session, base, done.id)
        requests = len(state.requests)
        before = crawl_row(session, done.id)
        snap = (before.status, before.stop_reason, before.elapsed_seconds, before.pages_crawled,
                before.finished_at, before.max_pages, before.last_checkpoint_at)
        page_snap = [(p.id, p.status, p.updated_at) for p in rows(session, done.id)]
        again = go(session, base, done.id)  # COMPLETED: harmless no-op
        assert again.pages == [] and again.stop_reason is StopReason.COMPLETED and len(again.queued) == 5
        after = crawl_row(session, done.id)
        assert snap == (after.status, after.stop_reason, after.elapsed_seconds, after.pages_crawled,
                        after.finished_at, after.max_pages, after.last_checkpoint_at)
        assert page_snap == [(p.id, p.status, p.updated_at) for p in rows(session, done.id)]

        failed, cancelled = new_crawl(session), new_crawl(session)
        repo = CrawlRepository(session)
        repo.set_status(failed, CrawlStatus.FAILED, error_message="x")
        repo.set_status(cancelled, CrawlStatus.CANCELLED)
        for terminal in (failed, cancelled):
            with pytest.raises(CrawlTerminalError):
                go(session, base, terminal.id)
            assert rows(session, terminal.id) == []
        assert len(state.requests) == requests  # not one extra request
    assert crawl_row(session, failed.id).status is CrawlStatus.FAILED
