"""Phase 6.3.3: read-only crawl state inspection (API + `crawl state` CLI). No network is used by the
inspection itself (the conftest blocks sockets); crawls are produced on loopback servers."""

from __future__ import annotations

import json

from sqlalchemy import select
from typer.testing import CliRunner

from app.cli.main import app
from app.core.config import get_settings
from app.crawler import CrawlStateReport, inspect_crawl_state
from app.db.session import get_engine
from app.models import CrawlStatus, Page, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_http_client import server
from tests.test_crawler_resume import SITE, db_url, file_db, local_network, run_crawl, snapshot  # noqa: F401,F811

runner = CliRunner()


def memory_crawl(session):
    domain, _ = DomainRepository(session).get_or_create("example.com")
    return CrawlRepository(session).create(domain.id, seed_url="https://example.com")


def test_fresh_state_reports_zero_counts(session):
    crawl = memory_crawl(session)
    report = inspect_crawl_state(session, crawl.id)
    assert isinstance(report, CrawlStateReport)
    assert report.status is CrawlStatus.PENDING and report.stop_reason is None
    assert (report.total_pages, report.discovered, report.visited, report.successful) == (0, 0, 0, 0)
    assert (report.failed, report.pending, report.skipped) == (0, 0, 0)
    assert report.started_at is None and report.finished_at is None and report.last_update_at is not None
    assert report.seed_url == "https://example.com"


def test_unknown_crawl_returns_none(session):
    assert inspect_crawl_state(session, 999) is None


def test_counts_each_page_status(session):
    crawl = memory_crawl(session)
    repo = PageRepository(session)
    for i, status in enumerate([PageStatus.FETCHED] * 2 + [PageStatus.FAILED, PageStatus.PENDING, PageStatus.SKIPPED]):
        repo.create(crawl.id, f"https://example.com/{i}", status=status)
    r = inspect_crawl_state(session, crawl.id)
    assert (r.total_pages, r.visited, r.successful, r.failed, r.pending, r.skipped) == (5, 3, 2, 1, 1, 1)
    assert r.discovered == 5


def test_inspection_is_deterministic_and_read_only(session):
    crawl = memory_crawl(session)
    PageRepository(session).create(crawl.id, "https://example.com/a")
    session.commit()
    before = [(p.id, p.status, p.updated_at) for p in session.scalars(select(Page))]
    crawl_updated = crawl.updated_at
    first = inspect_crawl_state(session, crawl.id)
    second = inspect_crawl_state(session, crawl.id)
    assert first == second and first.to_dict() == second.to_dict()
    assert not session.new and not session.dirty and not session.deleted
    assert [(p.id, p.status, p.updated_at) for p in session.scalars(select(Page))] == before
    assert crawl.updated_at == crawl_updated


def test_state_of_completed_and_interrupted_crawls(local_network, file_db):  # noqa: F811
    from sqlalchemy.orm import Session

    from app.db.session import create_db_engine

    url, cid = file_db

    def report():
        engine = create_db_engine(url)
        try:
            with Session(engine) as s:
                return inspect_crawl_state(s, cid)
        finally:
            engine.dispose()

    with server(SITE) as (base, state):
        run_crawl(url, cid, base, max_pages=2)
        requests = len(state.requests)
        part = report()
        assert part.status is CrawlStatus.RUNNING and part.stop_reason == "max_pages"
        assert (part.visited, part.successful, part.failed, part.pending, part.skipped) == (2, 2, 0, 0, 3)
        assert part.total_pages == 5 and part.started_at is not None and part.finished_at is None
        assert part.last_checkpoint_at is not None and part.current_depth >= 0
        assert len(state.requests) == requests  # inspecting does not request anything

        run_crawl(url, cid, base, max_pages=10)
        done = report()
    assert done.status is CrawlStatus.COMPLETED and done.stop_reason == "completed"
    assert (done.visited, done.successful, done.pending, done.skipped, done.total_pages) == (5, 5, 0, 0, 5)
    assert done.finished_at is not None and done.last_update_at >= part.last_update_at


def test_cli_crawl_state(monkeypatch, tmp_path):
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.db.session import create_db_engine

    url = f"sqlite:///{tmp_path / 'cli.db'}"
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        crawl = memory_crawl(s)
        repo = PageRepository(s)
        repo.create(crawl.id, "https://example.com/a", status=PageStatus.FETCHED)
        repo.create(crawl.id, "https://example.com/b", status=PageStatus.PENDING)
        s.commit()
        cid = crawl.id
    engine.dispose()
    monkeypatch.setenv("APP_DATABASE_URL", url)
    get_settings.cache_clear()
    get_engine.cache_clear()

    out = runner.invoke(app, ["crawl", "state", str(cid), "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert (data["crawl_id"], data["status"], data["visited"], data["pending"], data["total_pages"]) == (cid, "pending", 1, 1, 2)
    assert runner.invoke(app, ["crawl", "state", str(cid), "--json"]).output == out.output  # deterministic

    text = runner.invoke(app, ["crawl", "state", str(cid)])
    assert text.exit_code == 0 and "pending:" in text.output and "visited:" in text.output

    missing = runner.invoke(app, ["crawl", "state", "999"])
    assert missing.exit_code == 1 and "not found" in missing.output
    assert runner.invoke(app, ["crawl", "--help"]).exit_code == 0
