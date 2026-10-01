"""Phase 9.5: lead de-duplication integrated into the crawler, end to end and deterministic.

    loopback HTTP server -> AsyncHttpClient -> parsing -> extraction -> page-level leads -> canonical identity
    -> duplicate detection / multi-page aggregation (`LeadRepository.merge`) -> persistence (SQLite file)

No internet (the conftest blocks every other socket), a fake clock, fresh engine/session/crawler per run (a restart).
Site fixtures and helpers come from `tests/test_lead_pipeline_e2e.py`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crawler import CrawledPage, CrawlLifecycle, FetchOutcome, StopReason, inspect_crawl_state
from app.db.session import create_db_engine
from app.leads import BusinessLead, LeadCollector, aggregate_pages, lead_identity
from app.models import Domain, Lead, PageStatus
from app.repositories import CrawlRepository, DomainRepository, LeadRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML_HEADERS, server
from tests.test_lead_collector import HOME_HTML, crawled
from tests.test_lead_pipeline_e2e import (  # noqa: F401 (db_url is a fixture)
    ALL_EMAILS, ALL_PATHS, DOMAIN, HOME, ROUTES, T0, clock, content, db_url, lead_count, lead_of, new_crawl, paths, read,
)

IDENTITY = lead_identity(DOMAIN)


def run(url, crawl_id, base, *, seed="/", start=T0, wrap=None, **kwargs):
    """One crawl run in its own engine/session. `wrap(collector)` may replace the page observer."""
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            collector = LeadCollector(LeadRepository(s), now=clock(start))
            observer = wrap(collector) if wrap else collector

            async def main():
                async with make_crawler(
                    base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s),
                    commit_checkpoints=True, page_observer=observer, **kwargs,
                ) as crawler:
                    result = await crawler.crawl(base + seed, crawl_id=crawl_id)
                    return result, crawler.lifecycle

            outcome = asyncio.run(main())
            s.commit()
            return outcome
    finally:
        engine.dispose()


# ---------------------------------------------------------------- 1. every stage, one lead
def test_every_stage_agrees_and_the_stored_lead_equals_the_multi_page_aggregation(local_network, db_url):
    cid = new_crawl(db_url)
    built: list[BusinessLead] = []

    def record(collector):
        def observer(page: CrawledPage, crawl_id: int) -> None:
            lead = collector.build(page)
            if lead is not None:
                built.append(lead)
            collector(page, crawl_id)
        return observer

    with server(ROUTES) as (base, state):
        result, lifecycle = run(db_url, cid, base, wrap=record)
        requested = paths(state, base)

    # HTTP -> crawl
    assert result.stop_reason is StopReason.COMPLETED and lifecycle is CrawlLifecycle.COMPLETED
    assert sorted(requested) == ALL_PATHS
    # parsing/extraction: five HTML pages produced a page-level lead each, all with the same canonical identity
    assert len(built) == 5
    assert {lead.identity for lead in built} == {IDENTITY}
    assert {lead.identity.digest for lead in built} == {IDENTITY.digest}
    # page persistence
    statuses = read(db_url, lambda s: {p.url.removeprefix(base): p.status for p in PageRepository(s).list_for_crawl(cid)})
    assert sorted(statuses) == ALL_PATHS and sum(v is PageStatus.FETCHED for v in statuses.values()) == 5
    # one lead row, found through its identity, equal to the pure aggregation of the page-level leads
    assert lead_count(db_url) == 1
    stored = lead_of(db_url)
    assert read(db_url, lambda s: LeadRepository(s).get_by_domain(IDENTITY.domain).domain.name) == IDENTITY.domain
    assert stored.identity == IDENTITY
    aggregated = aggregate_pages(built)
    assert aggregated.conflicts == ()  # the site is consistent: nothing was rejected
    assert content(stored, base) == content(aggregated.lead, base)
    # several pages contributed, and the sources say which
    assert stored.emails == ALL_EMAILS
    sources = {k: [u.removeprefix(base) for u in v] for k, v in aggregated.value_sources.items()}
    assert sources["emails:info@acme.example"] == ["/contact"] and sources["emails:sales@acme.example"] == ["/about"]
    assert sources["emails:office@acme.example"] == ["/team"]
    assert aggregated.field_sources["address"].removeprefix(base) == "/contact"


# ---------------------------------------------------------------- 2. the seed page does not matter for the data
def test_seeding_at_an_inner_page_still_gives_one_complete_lead(local_network, db_url, tmp_path_factory):
    inner = dict(ROUTES)
    inner["/contact"] = (200, ROUTES["/contact"][1].replace(b"</body>", b"<a href='/'>Home</a><a href='/about'>About</a></body>"), HTML_HEADERS)
    cid = new_crawl(db_url)
    with server(inner) as (base, state):
        result, lifecycle = run(db_url, cid, base, seed="/contact")
        assert paths(state, base)[0] == "/contact"
    assert result.stop_reason is StopReason.COMPLETED and lifecycle is CrawlLifecycle.COMPLETED
    assert lead_count(db_url) == 1
    lead = lead_of(db_url)
    assert lead.emails == ALL_EMAILS and [p.number for p in lead.phones] == ["+442079460000"]
    assert lead.business_name == "Acme Plumbing" and lead.description == "Family plumbers since 1990."
    assert lead.address is not None and [s.platform for s in lead.social_profiles] == ["facebook"]


# ---------------------------------------------------------------- 3. repeated crawls
def test_repeated_crawls_are_deterministic_and_never_duplicate(local_network, tmp_path):
    contents = []
    for n in range(2):  # two independent databases, same site, same clock
        url = f"sqlite:///{tmp_path / f'run{n}.db'}"
        import app.models  # noqa: F401
        from app.db.base import Base

        engine = create_db_engine(url)
        Base.metadata.create_all(engine)
        engine.dispose()
        cid = new_crawl(url)
        with server(ROUTES) as (base, _):
            run(url, cid, base)
            run(url, new_crawl(url), base, start=T0 + timedelta(days=1))  # a second crawl of the same site
            assert lead_count(url) == 1
            contents.append(content(lead_of(url), base))
    assert contents[0] == contents[1]


def test_a_recrawl_of_a_changed_site_adds_new_data_keeps_old_data_and_reports_the_conflict(local_network, db_url, caplog):
    with server(ROUTES) as (base, _):
        run(db_url, new_crawl(db_url), base)
        before = lead_of(db_url)
    changed = dict(ROUTES)
    changed["/"] = (200, ROUTES["/"][1].replace(b"Acme Plumbing", b"Acme Heating"), HTML_HEADERS)
    changed["/team"] = (200, ROUTES["/team"][1].replace(b"office@", b"boss@"), HTML_HEADERS)
    with server(changed) as (base2, _):
        with caplog.at_level(logging.WARNING):
            run(db_url, new_crawl(db_url), base2, start=T0 + timedelta(days=2))
    after = lead_of(db_url)
    assert lead_count(db_url) == 1 and read(db_url, lambda s: DomainCount(s)) == 1
    assert after.business_name == "Acme Plumbing" == before.business_name  # not silently overwritten
    assert after.emails == tuple(sorted({*ALL_EMAILS, "boss@acme.example"}))  # new data added, old data kept
    assert any(r.message == "lead_conflict" and r.field == "business_name" for r in caplog.records)


def DomainCount(s) -> int:
    return s.scalar(select(func.count()).select_from(Domain)) or 0


# ---------------------------------------------------------------- 4. cancellation + resume + re-crawl
def test_cancel_resume_and_recrawl_end_with_one_correct_lead(local_network, db_url, tmp_path_factory):
    cid = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        def cancel_on_third(collector):
            seen = []

            def observer(page, crawl_id):
                seen.append(page.url)
                collector(page, crawl_id)
                if len(seen) == 3:
                    crawler_box[0].cancel()
            return observer

        crawler_box: list = []
        engine = create_db_engine(db_url)
        try:
            with Session(engine) as s:
                collector = LeadCollector(LeadRepository(s), now=clock(T0))
                observer = cancel_on_third(collector)

                async def main():
                    async with make_crawler(base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s),
                                            commit_checkpoints=True, page_observer=observer) as crawler:
                        crawler_box.append(crawler)
                        return await crawler.crawl(base + "/", crawl_id=cid), crawler.lifecycle

                cancelled, lifecycle = asyncio.run(main())
                s.commit()
        finally:
            engine.dispose()
        assert cancelled.stop_reason is StopReason.CANCELLED and lifecycle is CrawlLifecycle.STOPPED
        assert lead_count(db_url) == 1
        partial = lead_of(db_url)
        assert partial.emails != ALL_EMAILS and partial.emails  # some pages contributed, not all yet

        resumed, resumed_lifecycle = run(db_url, cid, base, start=T0 + timedelta(hours=1))
        assert resumed.stop_reason is StopReason.COMPLETED and resumed_lifecycle is CrawlLifecycle.COMPLETED
        assert sorted(paths(state, base)) == ALL_PATHS  # nothing fetched twice across the cancel and the resume
        complete = lead_of(db_url)

        run(db_url, new_crawl(db_url), base, start=T0 + timedelta(days=1))  # then a brand new crawl
        assert lead_count(db_url) == 1
        assert content(lead_of(db_url), base) == content(complete, base)
        assert complete.emails == ALL_EMAILS
    assert read(db_url, lambda s: inspect_crawl_state(s, cid).lifecycle) is CrawlLifecycle.COMPLETED


# ---------------------------------------------------------------- 5. canonical identity inside the pipeline (no network)
def make_crawl(session) -> int:
    domain, _ = DomainRepository(session).get_or_create(DOMAIN)
    return CrawlRepository(session).create(domain.id, seed_url="http://127.0.0.1").id


def test_every_spelling_of_one_site_is_one_lead_and_other_sites_stay_separate(session):
    repo = LeadRepository(session)
    collector = LeadCollector(repo, now=lambda: T0)
    crawl_id = make_crawl(session)
    page = lambda url, html=HOME_HTML, **kw: collector(crawled(url, html=html, **kw), crawl_id)  # noqa: E731

    page("https://www.acme.example/")
    page("http://acme.example/contact", "<html lang='en'><body><p>Call +44 20 7946 0000</p></body></html>")
    page("https://ACME.example:443/about", "<html lang='en'><body><p>Mail sales@acme.example</p></body></html>")
    page("https://acme.example/old", "<html lang='en'><body><p>Mail help@acme.example</p></body></html>",
         final_url="https://www.acme.example/help")
    page("https://acme-plumbing.example/")  # a similar name, a different site
    page("https://sub.acme-plumbing.example/")  # same registrable domain as the previous one

    assert session.scalar(select(func.count()).select_from(Lead)) == 2
    acme = repo.load("acme.example")
    assert acme.emails == ("help@acme.example", "info@acme.example", "sales@acme.example")
    assert [p.number for p in acme.phones] == ["+442079460000"]
    assert acme.website == "https://www.acme.example/"  # the first sighting's website is kept
    assert repo.load("acme-plumbing.example") is not None


def test_a_redirect_to_another_site_belongs_to_the_site_that_served_the_content(session):
    repo = LeadRepository(session)
    LeadCollector(repo, now=lambda: T0)(
        crawled("https://old-brand.example/", final_url="https://new-brand.example/"), make_crawl(session),
    )
    assert repo.load("old-brand.example") is None and repo.load("new-brand.example") is not None
    assert repo.count() == 1


def test_pages_that_are_not_ok_never_reach_deduplication(session):
    repo = LeadRepository(session)
    collector = LeadCollector(repo, now=lambda: T0)
    collector(crawled(outcome=FetchOutcome.HTTP_ERROR), 1)
    collector(crawled(html=None), 1)
    assert repo.count() == 0
