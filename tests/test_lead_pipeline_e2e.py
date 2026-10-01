"""Phase 8.5: end-to-end lead creation.

    loopback HTTP server -> AsyncHttpClient -> HTML parsing -> contact/structured extraction -> page analysis
    -> business identity -> lead aggregation -> lead persistence (SQLite file)

Deterministic: a fixed local site served on 127.0.0.1 (no internet, the conftest blocks every other socket), a fake
clock for `first_seen` / `last_seen`, a file SQLite database. Every "restart" uses a new engine, session, crawler and
`LeadCollector`, so only what was committed to the database survives, like a new process.

Site (the homepage links to everything except /team, which /about links to; depth 2):

    /          Acme Plumbing homepage, description
    /contact   info@acme.example, +44 20 7946 0000, an address
    /about     sales@acme.example, a Facebook link, links to /team
    /services  nothing extra
    /team      office@acme.example
    /missing   404 (linked from the homepage)
    /feed      application/json (linked from the homepage: not HTML)
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.crawler import CrawlLifecycle, CrawlPersistenceError, StopReason, inspect_crawl_state
from app.db.base import Base
from app.db.session import create_db_engine
from app.leads import LeadCollector
from app.models import CrawlStatus, PageStatus
from app.repositories import CrawlRepository, DomainRepository, LeadRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML_HEADERS, server

DOMAIN = "127.0.0.1"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ALL_PATHS = ["/", "/about", "/contact", "/feed", "/missing", "/services", "/team"]

HOME = (b"<html lang='en'><head><title>Acme Plumbing</title><meta name='description' content='Family plumbers since 1990.'>"
        b"</head><body><h1>Acme Plumbing</h1><a href='/contact'>Contact</a> <a href='/about'>About</a> "
        b"<a href='/services'>Services</a> <a href='/missing'>Old page</a> <a href='/feed'>Feed</a></body></html>")
CONTACT = (b"<html lang='en'><head><title>Contact us | Acme Plumbing</title></head><body><h1>Contact us</h1>"
           b"<p>Email info@acme.example or call <a href='tel:+442079460000'>+44 20 7946 0000</a>.</p>"
           b"<address>12 High Street, London</address></body></html>")
ABOUT = (b"<html lang='en'><head><title>About us | Acme Plumbing</title></head><body><h1>About us</h1>"
         b"<p>Sales: sales@acme.example</p><a href='https://www.facebook.com/acmeplumbing'>Facebook</a>"
         b"<a href='/team'>Our team</a></body></html>")
SERVICES = (b"<html lang='en'><head><title>Our services | Acme Plumbing</title></head><body><h1>Our services</h1>"
            b"<p>Boilers, bathrooms and emergency repairs.</p></body></html>")
TEAM = (b"<html lang='en'><head><title>Our team | Acme Plumbing</title></head><body><h1>Our team</h1>"
        b"<p>Reach the office at office@acme.example.</p></body></html>")

ROUTES = {
    "/": (200, HOME, HTML_HEADERS), "/contact": (200, CONTACT, HTML_HEADERS), "/about": (200, ABOUT, HTML_HEADERS),
    "/services": (200, SERVICES, HTML_HEADERS), "/team": (200, TEAM, HTML_HEADERS),
    "/feed": (200, b"{}", {"Content-Type": "application/json"}),
}
ALL_EMAILS = ("info@acme.example", "office@acme.example", "sales@acme.example")


# ---------------------------------------------------------------- helpers
def clock(start: datetime):
    ticks = itertools.count()
    return lambda: start + timedelta(minutes=next(ticks))


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    import app.models  # noqa: F401

    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def new_crawl(url: str) -> int:
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            domain, _ = DomainRepository(s).get_or_create(DOMAIN)
            crawl = CrawlRepository(s).create(domain.id, seed_url="http://127.0.0.1")
            s.commit()
            return crawl.id
    finally:
        engine.dispose()


def run_pipeline(url, crawl_id, base, *, start=T0, after_page=None, fail_on_call=None, observer=None, **kwargs):
    """One crawl run in its own engine/session (a fresh process). Pages and leads are committed after every page.

    Returns (CrawlResult, lifecycle). `after_page(n, crawler)` runs after the n-th page's lead was written;
    `fail_on_call=n` makes the n-th lead write fail with a database error."""
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            collector = LeadCollector(LeadRepository(s), now=clock(start))
            holder: list = []
            calls: list[str] = []

            def page_observer(page, crawl):
                calls.append(page.url)
                if fail_on_call == len(calls):
                    raise OperationalError("INSERT INTO leads", {}, Exception("disk I/O error"))
                collector(page, crawl)
                if after_page is not None:
                    after_page(len(calls), holder[0])

            async def main():
                async with make_crawler(
                    base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s),
                    commit_checkpoints=True, page_observer=observer or page_observer, **kwargs,
                ) as crawler:
                    holder.append(crawler)
                    result = await crawler.crawl(base + "/", crawl_id=crawl_id)
                    return result, crawler.lifecycle

            outcome = asyncio.run(main())
            s.commit()
            return outcome
    finally:
        engine.dispose()


def read(url, fn):
    """Run `fn(session)` on a brand-new engine/session (what a restarted process would see)."""
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            return fn(s)
    finally:
        engine.dispose()


def paths(state, base):
    return [r.removeprefix(base) for r in state.requests]


def lead_of(url):
    return read(url, lambda s: LeadRepository(s).load(DOMAIN))


def lead_count(url):
    return read(url, lambda s: LeadRepository(s).count())


def content(lead, base):
    """Everything except the seen range (which legitimately differs between runs) and the server's port
    (every `server()` listens on a new ephemeral port): URLs are compared relative to `base`."""
    data = {**lead.to_dict(), "first_seen": None, "last_seen": None}
    return json.loads(json.dumps(data).replace(base, "http://SITE"))


def uninterrupted_content(tmp_path_factory):
    """`content()` of the lead of one uninterrupted crawl of the site (to compare interrupted crawls with)."""
    url = f"sqlite:///{tmp_path_factory.mktemp('baseline') / 'baseline.db'}"
    import app.models  # noqa: F401

    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    cid = new_crawl(url)
    with server(ROUTES) as (base, _):
        run_pipeline(url, cid, base)
    return content(lead_of(url), base)


# ---------------------------------------------------------------- 1. crawl -> parse -> extract -> lead -> persist
def test_crawl_creates_pages_and_one_lead(local_network, db_url):
    cid = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        result, lifecycle = run_pipeline(db_url, cid, base)
        requested = paths(state, base)

    # crawl works, links are discovered (/team only through /about, depth 2), lifecycle is correct
    assert result.stop_reason is StopReason.COMPLETED and lifecycle is CrawlLifecycle.COMPLETED
    assert requested[0] == "/" and sorted(requested) == ALL_PATHS
    assert len(requested) == len(set(requested))  # every URL requested once

    # pages are persisted
    def pages(s):
        rows = PageRepository(s).list_for_crawl(cid)
        return {p.url.removeprefix(base): p.status for p in rows}, inspect_crawl_state(s, cid)

    statuses, report = read(db_url, pages)
    assert sorted(statuses) == ALL_PATHS
    assert statuses["/feed"] is PageStatus.FAILED and statuses["/missing"] is PageStatus.FAILED
    assert all(v is PageStatus.FETCHED for k, v in statuses.items() if k not in ("/feed", "/missing"))
    assert report.lifecycle is CrawlLifecycle.COMPLETED and report.pending == 0 and report.visited == 7

    # the lead is created and persisted from the five HTML pages
    assert lead_count(db_url) == 1
    lead = lead_of(db_url)
    assert lead.domain == DOMAIN and lead.website == base + "/"
    assert lead.business_name == "Acme Plumbing" and lead.description == "Family plumbers since 1990."
    assert lead.emails == ALL_EMAILS
    assert [p.number for p in lead.phones] == ["+442079460000"]
    assert lead.address is not None and lead.address.formatted == "12 High Street, London"
    assert [(s.platform, s.url) for s in lead.social_profiles] == [("facebook", "https://www.facebook.com/acmeplumbing")]
    assert lead.page_type.value == "homepage" and lead.language == "en" and lead.source_url == base + "/"
    assert lead.first_seen == T0 and lead.last_seen == T0 + timedelta(minutes=4)  # five HTML pages, one tick each
    assert read(db_url, lambda s: LeadRepository(s).get_by_domain(DOMAIN).crawl_id) == cid


# ---------------------------------------------------------------- 2. restart, resume, no duplicate lead, no refetch
def test_restart_and_resume_keeps_one_lead_and_never_refetches(local_network, db_url, tmp_path_factory):
    baseline = uninterrupted_content(tmp_path_factory)
    cid = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        first, first_lifecycle = run_pipeline(db_url, cid, base, max_pages=2)  # visits "/" and "/contact", then stops
        after_first = paths(state, base)
        lead_after_first = lead_of(db_url)

        assert first.stop_reason is StopReason.MAX_PAGES and first_lifecycle is CrawlLifecycle.STOPPED
        assert after_first == ["/", "/contact"]
        assert lead_count(db_url) == 1 and lead_after_first.emails == ("info@acme.example",)
        assert read(db_url, lambda s: inspect_crawl_state(s, cid).lifecycle) is CrawlLifecycle.STOPPED

        # restart: new engine, session, crawler and collector; same crawl id, higher limit
        second, second_lifecycle = run_pipeline(db_url, cid, base, start=T0 + timedelta(hours=1))
        all_requests = paths(state, base)

    assert second.stop_reason is StopReason.COMPLETED and second_lifecycle is CrawlLifecycle.COMPLETED
    assert [u.removeprefix(base) for u in second.previously_visited] == ["/", "/contact"]
    assert all_requests[:2] == ["/", "/contact"]
    assert sorted(all_requests) == ALL_PATHS  # nothing requested twice: the visited pages were not fetched again
    assert all_requests.count("/") == 1 and all_requests.count("/contact") == 1

    # no duplicate lead; the second run added what it found to the first run's lead
    assert lead_count(db_url) == 1
    resumed = lead_of(db_url)
    assert resumed.emails == ALL_EMAILS
    assert content(resumed, base) == baseline  # the same lead as one uninterrupted crawl produces
    assert resumed.first_seen == T0  # never moves forward
    assert resumed.last_seen > T0 + timedelta(hours=1)

    pages = read(db_url, lambda s: PageRepository(s).count_for_crawl(cid))
    assert pages == 7
    assert read(db_url, lambda s: inspect_crawl_state(s, cid).lifecycle) is CrawlLifecycle.COMPLETED


# ---------------------------------------------------------------- 3. duplicates across crawls and re-runs
def test_a_second_crawl_of_the_same_site_updates_the_same_lead(local_network, db_url):
    first_id = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        run_pipeline(db_url, first_id, base)
        before = lead_of(db_url)
        second_id = new_crawl(db_url)
        run_pipeline(db_url, second_id, base, start=T0 + timedelta(days=1))
        assert len(paths(state, base)) == 14  # a new crawl is a new crawl: every page is requested again

    assert lead_count(db_url) == 1  # but there is still exactly one lead for the domain
    after = lead_of(db_url)
    assert content(after, base) == content(before, base)
    assert after.first_seen == T0 and after.last_seen > T0 + timedelta(days=1)
    assert read(db_url, lambda s: LeadRepository(s).get_by_domain(DOMAIN).crawl_id) == second_id
    assert read(db_url, lambda s: DomainRepository(s).count()) == 1


def test_running_a_completed_crawl_again_changes_nothing(local_network, db_url):
    cid = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        run_pipeline(db_url, cid, base)
        requests_before = list(state.requests)
        row_before = read(db_url, lambda s: LeadRepository(s).get_by_domain(DOMAIN).updated_at)
        lead_before = lead_of(db_url)

        result, lifecycle = run_pipeline(db_url, cid, base, start=T0 + timedelta(days=9))

        assert state.requests == requests_before  # nothing fetched
    assert lifecycle is CrawlLifecycle.COMPLETED and result.pages == []
    assert lead_of(db_url) == lead_before
    assert read(db_url, lambda s: LeadRepository(s).get_by_domain(DOMAIN).updated_at) == row_before
    assert lead_count(db_url) == 1


# ---------------------------------------------------------------- 4. cancellation
def test_cancellation_is_safe_and_the_crawl_resumes(local_network, db_url, tmp_path_factory):
    baseline = uninterrupted_content(tmp_path_factory)
    cid = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        cancelled, lifecycle = run_pipeline(
            db_url, cid, base, after_page=lambda n, crawler: crawler.cancel() if n == 2 else None,
        )
        assert cancelled.stop_reason is StopReason.CANCELLED and lifecycle is CrawlLifecycle.STOPPED
        assert paths(state, base) == ["/", "/contact"]  # nothing new was started after cancel()

        # the pages visited before the cancel are persisted together with their lead data
        report = read(db_url, lambda s: inspect_crawl_state(s, cid))
        assert report.lifecycle is CrawlLifecycle.STOPPED and report.stop_reason == "cancelled"
        assert report.successful == 2 and report.pending >= 1
        assert read(db_url, lambda s: CrawlRepository(s).require(cid).status) is CrawlStatus.RUNNING  # resumable
        assert lead_count(db_url) == 1 and lead_of(db_url).emails == ("info@acme.example",)

        resumed, resumed_lifecycle = run_pipeline(db_url, cid, base, start=T0 + timedelta(hours=2))
        requested = paths(state, base)

    assert resumed.stop_reason is StopReason.COMPLETED and resumed_lifecycle is CrawlLifecycle.COMPLETED
    assert sorted(requested) == ALL_PATHS  # each URL exactly once across both runs
    assert lead_count(db_url) == 1
    assert content(lead_of(db_url), base) == baseline


# ---------------------------------------------------------------- 5. persistence failure: page and lead stay consistent
def test_a_failed_lead_write_rolls_the_page_back_and_resume_loses_nothing(local_network, db_url, tmp_path_factory):
    baseline = uninterrupted_content(tmp_path_factory)
    cid = new_crawl(db_url)
    with server(ROUTES) as (base, state):
        with pytest.raises(CrawlPersistenceError):
            run_pipeline(db_url, cid, base, fail_on_call=2)  # the lead write of "/contact" fails
        assert paths(state, base)[:2] == ["/", "/contact"]

        def after_failure(s):
            statuses = {p.url.removeprefix(base): p.status for p in PageRepository(s).list_for_crawl(cid)}
            return statuses, CrawlRepository(s).require(cid).status

        statuses, crawl_status = read(db_url, after_failure)
        assert statuses["/"] is PageStatus.FETCHED  # committed together with its lead
        assert statuses["/contact"] is PageStatus.PENDING  # fetched, but NOT recorded as visited: it is retried
        assert crawl_status is CrawlStatus.RUNNING  # still resumable
        assert lead_count(db_url) == 1 and lead_of(db_url).emails == ()  # only the seed's data

        resumed, _ = run_pipeline(db_url, cid, base, start=T0 + timedelta(hours=3))
        requested = paths(state, base)

    assert resumed.stop_reason is StopReason.COMPLETED
    assert requested.count("/") == 1  # the committed seed page is never requested again
    assert requested.count("/contact") == 2  # the rolled-back page is
    assert lead_count(db_url) == 1 and content(lead_of(db_url), base) == baseline  # nothing lost


def test_an_observer_bug_does_not_stop_the_crawl(local_network, db_url):
    cid = new_crawl(db_url)

    def broken(page, crawl):
        raise RuntimeError("bug in a downstream consumer")

    with server(ROUTES) as (base, state):
        result, lifecycle = run_pipeline(db_url, cid, base, observer=broken)

    assert result.stop_reason is StopReason.COMPLETED and lifecycle is CrawlLifecycle.COMPLETED
    assert sorted(paths(state, base)) == ALL_PATHS
    assert lead_count(db_url) == 0
    assert read(db_url, lambda s: PageRepository(s).count_for_crawl(cid, status=PageStatus.FETCHED)) == 5


# ---------------------------------------------------------------- 6. nothing usable, no lead
@pytest.mark.parametrize("routes", [
    {},  # the seed answers 404
    {"/": (200, b"{}", {"Content-Type": "application/json"})},  # the seed is not HTML
], ids=["seed-404", "seed-not-html"])
def test_a_site_without_a_usable_page_creates_no_lead(local_network, db_url, routes):
    cid = new_crawl(db_url)
    with server(routes) as (base, _):
        result, lifecycle = run_pipeline(db_url, cid, base)
    assert result.stop_reason is StopReason.COMPLETED and lifecycle is CrawlLifecycle.COMPLETED
    assert lead_count(db_url) == 0
    assert read(db_url, lambda s: PageRepository(s).count_for_crawl(cid)) == 1


# ---------------------------------------------------------------- 7. a crawl without the observer is unchanged
def test_a_crawl_without_the_observer_creates_no_lead_and_behaves_as_before(local_network, db_url):
    cid = new_crawl(db_url)

    async def main(base, s):
        async with make_crawler(base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s)) as crawler:
            return await crawler.crawl(base + "/", crawl_id=cid)

    engine = create_db_engine(db_url)
    try:
        with server(ROUTES) as (base, state):
            with Session(engine) as s:
                result = asyncio.run(main(base, s))
                s.commit()
            assert sorted(paths(state, base)) == ALL_PATHS
    finally:
        engine.dispose()
    assert result.stop_reason is StopReason.COMPLETED and len(result.fetched) == 5 and len(result.failed) == 2
    assert lead_count(db_url) == 0
