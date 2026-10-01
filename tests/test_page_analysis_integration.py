"""Phase 7.3.4: page analysis integrated in the BFS crawler (loopback server + SQLite, no internet).

HTTP response -> HTML parsing -> contact extraction -> structured data -> language -> classification ->
metrics -> existing persistence/result handling. Expected values are derived from the HTML written here,
not from the parser's own output.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.crawler.html_parser as html_parser
from app.crawler import PageCategory, StopReason
from app.db.base import Base
from app.db.session import create_db_engine
from app.models import CrawlStatus, Page, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML_HEADERS, server

CONTACT_HTML = """<html lang="en"><head><title>Contact us | Acme Plumbing</title>
<meta name="description" content="Get in touch with Acme Plumbing">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"Acme Plumbing",
"telephone":"+1 415 555 0134","url":"https://acme.example/","sameAs":["https://www.linkedin.com/company/acme-plumbing"],
"address":{"@type":"PostalAddress","streetAddress":"1 Main St","addressLocality":"Springfield","addressCountry":"US"}}</script>
</head><body><h1>Contact us</h1>
<p>Questions? Write to <a href="mailto:hello@acme.example">hello@acme.example</a> or call +1 415 555 0199.</p>
<p>Follow us on <a href="https://twitter.com/acmeplumbing">X</a>.</p>
<img src="/logo.png"><a href="/">Home</a><a href="/about">About</a><a href="/team">Team</a></body></html>"""


def html(title: str, *hrefs: str) -> tuple[int, bytes, dict[str, str]]:
    links = "".join(f'<a href="{h}">l</a>' for h in hrefs)
    return 200, f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{links}</body></html>".encode(), HTML_HEADERS


# "/" -> /contact, /about ; /contact -> /, /about, /team ; /about -> / ; /team -> /team/deep ; /team/deep -> (depth 3)
SITE = {
    "/": html("Acme", "/contact", "/about"),
    "/contact": (200, CONTACT_HTML.encode(), HTML_HEADERS),
    "/about": html("About Acme", "/"),
    "/team": html("Our team", "/team/deep"),
    "/team/deep": html("Deep"),
}
ALL = ["/", "/about", "/contact", "/team", "/team/deep"]


def run(base, *, session=None, crawl_id=None, setup=None, **kwargs):
    async def main():
        extra = {}
        if session is not None:
            extra = {"page_repository": PageRepository(session), "crawl_repository": CrawlRepository(session)}
        async with make_crawler(base, **extra, **kwargs) as crawler:
            if setup is not None:
                setup(crawler)
            return await crawler.crawl(base + "/", crawl_id=crawl_id)

    return asyncio.run(main())


def new_crawl(session):
    domain, _ = DomainRepository(session).get_or_create("127.0.0.1")
    session.flush()
    return CrawlRepository(session).create(domain.id, seed_url="http://127.0.0.1").id


def stored(session, crawl_id):
    return {p.url: p for p in session.scalars(select(Page).where(Page.crawl_id == crawl_id).order_by(Page.id))}


def by_path(result, base):
    return {p.url.removeprefix(base) or "/": p for p in result.pages}


# ---------------------------------------------------------------- every analysis result for one page
def test_one_crawled_page_carries_every_analysis_result(local_network):
    with server(SITE) as (base, _):
        result = run(base)
    assert result.stop_reason is StopReason.COMPLETED
    parsed = by_path(result, base)["/contact"].parsed
    assert parsed is not None

    # HTML parsing
    assert parsed.title == "Contact us | Acme Plumbing"
    assert [(h.level, h.text) for h in parsed.headings] == [(1, "Contact us")]
    assert parsed.metadata.description == "Get in touch with Acme Plumbing"
    assert parsed.encoding == "utf-8"
    # contact extraction (visible text / anchors only; the JSON-LD phone stays inside structured_data)
    assert parsed.emails == ("hello@acme.example",)
    assert [(p.number, p.raw) for p in parsed.phones] == [("+14155550199", "+1 415 555 0199")]
    assert [(s.platform, s.url) for s in parsed.social_links] == [("x", "https://x.com/acmeplumbing")]
    # structured data
    (business,) = parsed.structured_data
    assert (business.type, business.name) == ("LocalBusiness", "Acme Plumbing")
    assert [p.number for p in business.telephones] == ["+14155550134"]
    assert [(s.platform, s.url) for s in business.social_links] == [
        ("linkedin", "https://www.linkedin.com/company/acme-plumbing")
    ]
    (address,) = parsed.addresses
    assert (address.street, address.city, address.country) == ("1 Main St", "Springfield", "US")
    # language
    assert (parsed.language.language, parsed.language.source, parsed.language.declared) == ("en", "html_lang", "en")
    # classification: the URL is /contact, so the URL signal must have been available
    assert parsed.classification.category is PageCategory.CONTACT
    assert "url" in parsed.classification.signals and not parsed.classification.ambiguous
    # content metrics
    m = parsed.metrics
    assert (m.heading_count, m.image_count, m.email_count, m.phone_count) == (1, 1, 1, 1)
    assert (m.link_count, m.unique_link_count) == (5, 5)  # mailto, x.com, /, /about, /team
    assert m.character_count == len(parsed.text) and 0 < m.text_html_ratio < 1
    assert m.html_length == len(CONTACT_HTML)
    assert m.word_count > 5


def test_each_page_is_classified_with_its_own_url(local_network):
    with server(SITE) as (base, _):
        pages = by_path(run(base), base)
    assert pages["/"].parsed.classification.category is PageCategory.HOMEPAGE
    assert pages["/about"].parsed.classification.category is PageCategory.ABOUT
    assert pages["/contact"].parsed.classification.category is PageCategory.CONTACT


# ---------------------------------------------------------------- each stage runs once per page
def test_analysis_stages_run_exactly_once_per_html_page(local_network, monkeypatch):
    calls = {"language": 0, "classify": 0, "metrics": 0, "parse": 0}

    def counting(name, real):
        def wrapper(*a, **k):
            calls[name] += 1
            return real(*a, **k)
        return wrapper

    monkeypatch.setattr(html_parser, "detect_language", counting("language", html_parser.detect_language))
    monkeypatch.setattr(html_parser, "classify_page", counting("classify", html_parser.classify_page))
    monkeypatch.setattr(html_parser, "compute_metrics", counting("metrics", html_parser.compute_metrics))
    monkeypatch.setattr(html_parser, "parse_html", counting("parse", html_parser.parse_html))
    with server({**SITE, "/data.json": (200, b"{}", {"Content-Type": "application/json"}), "/team/deep": html("Deep", "/data.json")}) as (base, _):
        result = run(base)
    html_pages = [p for p in result.pages if p.parsed is not None]
    assert len(html_pages) == 5
    assert calls == {"language": 5, "classify": 5, "metrics": 5, "parse": 5}


def test_non_html_and_failed_pages_are_not_analysed(local_network):
    routes = {
        "/": html("Home", "/data", "/missing"),
        "/data": (200, b'{"a": "hello@x.example"}', {"Content-Type": "application/json"}),
        "/missing": (404, b"<title>x</title>", HTML_HEADERS),
    }
    with server(routes) as (base, _):
        pages = by_path(run(base), base)
    assert pages["/"].parsed is not None
    assert pages["/data"].parsed is None and pages["/missing"].parsed is None


# ---------------------------------------------------------------- crawling and discovery unchanged
def test_crawl_and_link_discovery_are_identical_with_and_without_analysis(local_network, monkeypatch):
    import app.crawler.bfs as bfs

    with server(SITE) as (base, state):
        with_analysis = run(base)
        requests_with = list(state.requests)
        state.requests.clear()
        monkeypatch.setattr(bfs, "parse_response", lambda _r: None)  # reference: no analysis at all
        without = run(base)
        requests_without = list(state.requests)
    assert with_analysis.visited == without.visited and with_analysis.queued == without.queued
    assert [(p.depth, p.parent, p.links_found, p.links_queued) for p in with_analysis.pages] == [
        (p.depth, p.parent, p.links_found, p.links_queued) for p in without.pages
    ]
    assert requests_with == requests_without == ["/", "/contact", "/about", "/team", "/team/deep"]
    assert all(p.parsed is None for p in without.pages) and all(p.parsed is not None for p in with_analysis.pages)


def test_links_are_discovered_from_the_analysed_page(local_network):
    with server(SITE) as (base, _):
        result = run(base)
    contact = by_path(result, base)["/contact"]
    # candidates: twitter.com (external), /, /about, /team (mailto is not a candidate); only /team is new
    assert (contact.links_found, contact.links_queued) == (4, 1)
    assert result.rejections  # the external profile link was rejected by the URL engine, as before
    assert result.queued == [base + p for p in ("/", "/contact", "/about", "/team", "/team/deep")]
    assert contact.parent == base + "/" and contact.depth == 1


@pytest.mark.parametrize("kwargs,expected_paths,stop", [
    ({"max_depth": 1}, ["/", "/about", "/contact"], StopReason.COMPLETED),
    ({"max_pages": 2}, ["/", "/contact"], StopReason.MAX_PAGES),
    ({"max_depth": 0}, ["/"], StopReason.COMPLETED),
    ({"request_delay": 0.05}, ALL, StopReason.COMPLETED),
    ({"max_crawl_time": 30.0}, ALL, StopReason.COMPLETED),
])
def test_limits_still_apply_and_pages_are_still_analysed(local_network, kwargs, expected_paths, stop):
    with server(SITE) as (base, state):
        result = run(base, **kwargs)
        requests = list(state.requests)
    assert result.stop_reason is stop
    assert sorted(p.url.removeprefix(base) or "/" for p in result.pages) == sorted(expected_paths)
    assert len(requests) == len(set(requests)) == len(expected_paths)
    assert all(p.parsed is not None and p.parsed.metrics.character_count > 0 for p in result.pages)


def test_request_delay_is_still_enforced(local_network):
    import time

    with server(SITE) as (base, _):
        start = time.monotonic()
        run(base, request_delay=0.1)
    assert time.monotonic() - start >= 0.4 - 0.05  # 5 requests -> 4 gaps


# ---------------------------------------------------------------- no duplicate requests
def test_no_duplicate_requests_and_no_extra_requests_for_analysis(local_network):
    with server(SITE) as (base, state):
        result = run(base)
    assert sorted(state.requests) == sorted(p for p in ALL)  # exactly one request per page, nothing else (no robots/favicon)
    assert len(result.pages) == len(state.requests) == 5


# ---------------------------------------------------------------- persistence
def test_persistence_still_stores_pages_with_titles(local_network, session):
    cid = new_crawl(session)
    with server(SITE) as (base, state):
        result = run(base, session=session, crawl_id=cid)
        rows = stored(session, cid)
    assert result.stop_reason is StopReason.COMPLETED
    assert {u.removeprefix(base) or "/" for u in rows} == set(ALL)
    assert {p.status for p in rows.values()} == {PageStatus.FETCHED}
    assert rows[base + "/contact"].title == "Contact us | Acme Plumbing"
    assert rows[base + "/contact"].http_status == 200
    assert CrawlRepository(session).require(cid).status is CrawlStatus.COMPLETED
    assert CrawlRepository(session).require(cid).pages_crawled == 5
    assert len(state.requests) == 5


def test_analysis_failure_never_changes_what_is_persisted(local_network, session, monkeypatch):
    cid = new_crawl(session)
    monkeypatch.setattr(html_parser, "classify_page", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(html_parser, "compute_metrics", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(html_parser, "detect_language", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with server(SITE) as (base, state):
        result = run(base, session=session, crawl_id=cid)
        rows = stored(session, cid)
    assert result.stop_reason is StopReason.COMPLETED and len(state.requests) == 5
    assert {p.status for p in rows.values()} == {PageStatus.FETCHED}
    assert rows[base + "/contact"].title == "Contact us | Acme Plumbing"
    parsed = by_path(result, base)["/contact"].parsed
    assert parsed.emails == ("hello@acme.example",)  # the other stages still delivered
    assert parsed.classification.category is PageCategory.OTHER  # fell back, did not raise
    assert parsed.metrics.character_count == 0


# ---------------------------------------------------------------- resume
@pytest.fixture
def file_db(tmp_path):
    import app.models  # noqa: F401

    url = f"sqlite:///{tmp_path / 'analysis.db'}"
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        cid = new_crawl(s)
        s.commit()
    engine.dispose()
    return url, cid


def run_on_db(url, cid, base, *, setup=None, **kwargs):
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            result = run(base, session=s, crawl_id=cid, setup=setup, commit_checkpoints=True, **kwargs)
            s.commit()
            return result
    finally:
        engine.dispose()


def test_resume_still_works_and_resumed_pages_are_analysed(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        first = run_on_db(url, cid, base, max_pages=2)
        after_first = list(state.requests)
        second = run_on_db(url, cid, base, max_pages=10)
        everything = list(state.requests)
    assert first.stop_reason is StopReason.MAX_PAGES and len(first.pages) == 2
    assert second.stop_reason is StopReason.COMPLETED
    assert after_first == ["/", "/contact"]
    assert everything[:2] == after_first and sorted(everything) == sorted(ALL)  # no duplicates across runs
    assert {u.removeprefix(base) or "/" for u in second.previously_visited} == {"/", "/contact"}
    assert len(second.pages) == 3 and all(p.parsed is not None for p in second.pages)
    deep = by_path(second, base)["/team/deep"].parsed
    assert deep.title == "Deep" and deep.classification is not None and deep.metrics.heading_count == 1
    engine = create_db_engine(url)
    with Session(engine) as s:
        assert {p.title for p in stored(s, cid).values()} == {
            "Acme", "Contact us | Acme Plumbing", "About Acme", "Our team", "Deep"}
    engine.dispose()


def test_a_completed_crawl_is_not_requested_again(local_network, file_db):
    url, cid = file_db
    with server(SITE) as (base, state):
        run_on_db(url, cid, base)
        count = len(state.requests)
        again = run_on_db(url, cid, base)
        assert len(state.requests) == count == 5
    assert again.pages == [] and again.stop_reason is StopReason.COMPLETED


# ---------------------------------------------------------------- cancellation
def test_cancellation_still_works_with_analysis_and_resumes_without_duplicates(local_network, file_db):
    url, cid = file_db

    def cancel_after_two(crawler):
        real, seen = crawler._persist_page, {"n": 0}

        def wrapper(*a, **k):
            real(*a, **k)
            seen["n"] += 1
            if seen["n"] == 2:
                crawler.cancel()

        crawler._persist_page = wrapper

    with server(SITE) as (base, state):
        first = run_on_db(url, cid, base, setup=cancel_after_two)
        after_cancel = list(state.requests)
        second = run_on_db(url, cid, base)
        everything = list(state.requests)
    assert first.stop_reason is StopReason.CANCELLED and len(first.pages) == 2
    assert after_cancel == ["/", "/contact"]
    assert all(p.parsed is not None for p in first.pages)  # the pages fetched before the cancel were analysed
    assert second.stop_reason is StopReason.COMPLETED
    assert sorted(everything) == sorted(ALL)  # every page requested exactly once over both runs
    assert [p.parsed is not None for p in second.pages] == [True] * 3
