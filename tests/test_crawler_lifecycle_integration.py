"""Phase 6.4 end to end: start, visit pages, cancel, inspect the stored state, resume with a new crawler
(new engine and session, like a new process), finish. Loopback server, file SQLite, fully deterministic."""

from __future__ import annotations

import asyncio
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crawler import CrawlLifecycle, StopReason, inspect_crawl_state
from app.db.base import Base
from app.db.session import create_db_engine
from app.models import CrawlStatus, Page, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler, page  # noqa: F401 (fixture)
from tests.test_crawler_http_client import server

SITE = {
    "/": page("/a", "/b", "/c"),
    "/a": page("/a1", "/b"),
    "/b": page("/b1"),
    "/c": page(),
    "/a1": page(),
    "/b1": page(),
}
ALL = ["/", "/a", "/a1", "/b", "/b1", "/c"]


def run(url, cid, base, *, cancel_after=None):
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            async def main():
                async with make_crawler(
                    base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s),
                    commit_checkpoints=True,
                ) as crawler:
                    if cancel_after is not None:
                        real, seen = crawler._persist_page, {"n": 0}

                        def wrapper(*a, **k):
                            real(*a, **k)
                            seen["n"] += 1
                            if seen["n"] == cancel_after:
                                crawler.cancel()

                        crawler._persist_page = wrapper
                    result = await crawler.crawl(base + "/", crawl_id=cid)
                    return result, crawler.lifecycle

            return asyncio.run(main())
    finally:
        engine.dispose()


def inspect(url, cid):
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            report = inspect_crawl_state(s, cid)
            pages = {p.url: p.status for p in s.scalars(select(Page).where(Page.crawl_id == cid))}
            return report, pages
    finally:
        engine.dispose()


def test_start_cancel_inspect_resume_finish(local_network, tmp_path):
    url = f"sqlite:///{tmp_path / 'e2e.db'}"
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        domain, _ = DomainRepository(s).get_or_create("127.0.0.1")
        cid = CrawlRepository(s).create(domain.id, seed_url="http://127.0.0.1").id
        s.commit()
    engine.dispose()

    with server(SITE) as (base, state):
        # 1-3. start, visit several pages, cancel after the third
        first, lifecycle = run(url, cid, base, cancel_after=3)
        assert first.stop_reason is StopReason.CANCELLED and lifecycle is CrawlLifecycle.STOPPED
        visited_first = list(state.requests)
        assert len(visited_first) == 3

        # 4. persisted state
        report, pages = inspect(url, cid)
        assert report.lifecycle is CrawlLifecycle.STOPPED and report.stop_reason == "cancelled"
        assert report.status is CrawlStatus.RUNNING and report.finished_at is None
        assert (report.visited, report.successful, report.failed) == (3, 3, 0)
        assert report.pending >= 1 and report.total_pages == report.visited + report.pending + report.skipped
        assert sorted(p.removeprefix(base) for p, st in pages.items() if st is PageStatus.FETCHED) == sorted(
            r for r in visited_first
        )

        # 5-6. a new crawler resumes from the same state
        second, lifecycle = run(url, cid, base)
        assert second.stop_reason is StopReason.COMPLETED and lifecycle is CrawlLifecycle.COMPLETED

        # 7. nothing visited before was fetched again
        assert not set(visited_first) & set(state.requests[len(visited_first):])
        # 8. the remaining pages were processed, every URL requested exactly once overall
        assert Counter(state.requests) == Counter(ALL)
        assert sorted(p.url.removeprefix(base) for p in second.pages) == sorted(set(ALL) - set(visited_first))

    # 9. final lifecycle state and no duplicate records
    report, pages = inspect(url, cid)
    assert report.lifecycle is CrawlLifecycle.COMPLETED and report.status is CrawlStatus.COMPLETED
    assert report.stop_reason == "completed" and report.finished_at is not None
    assert (report.visited, report.pending, report.skipped, report.total_pages) == (6, 0, 0, 6)
    assert len(pages) == len(set(pages)) == 6
