"""Phase 7.1: HTML parsing inside the BFS crawler (loopback server + SQLite, no internet)."""

from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import select

from app.crawler import ParsedPage, StopReason
from app.models import Page, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_bfs import crawl, local_network, make_crawler  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML_HEADERS, server

PERSIAN_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


def doc(title: str, *hrefs: str, extra: str = "") -> tuple[int, bytes, dict[str, str]]:
    links = "".join(f'<a href="{h}">link</a>' for h in hrefs)
    html = f"<html><head><title>{title}</title>{extra}</head><body><h1>{title}</h1><p>text of {title}</p>{links}</body></html>"
    return 200, html.encode(), HTML_HEADERS


def new_crawl(session):
    domain, _ = DomainRepository(session).get_or_create("127.0.0.1")
    return CrawlRepository(session).create(domain.id, seed_url="http://127.0.0.1")


def crawl_into(session, base, crawl_id, **kwargs):
    async def main():
        async with make_crawler(base, page_repository=PageRepository(session), **kwargs) as crawler:
            return await crawler.crawl(base + "/", crawl_id=crawl_id)

    return asyncio.run(main())


def rows(session, crawl_id):
    return {p.url: p for p in session.scalars(select(Page).where(Page.crawl_id == crawl_id).order_by(Page.id))}


# ---------------------------------------------------------------- 1-3. fetch -> parse -> ParsedPage
def test_crawler_fetches_parses_and_produces_parsed_pages(local_network):
    routes = {
        "/": doc("Home", "/a", "b", extra='<meta name="description" content="Home page"><link rel="canonical" href="/">'),
        "/a": doc("Page A"),
        "/b": doc("Page B"),
    }
    with server(routes) as (base, _):
        result = crawl(base)
    assert result.stop_reason is StopReason.COMPLETED and len(result.fetched) == 3
    by_title = {p.parsed.title: p for p in result.pages}
    assert set(by_title) == {"Home", "Page A", "Page B"}
    home = by_title["Home"].parsed
    assert isinstance(home, ParsedPage)
    assert home.text.splitlines() == ["Home", "text of Home", "linklink"]  # adjacent inline links join, as in a browser
    assert [(h.level, h.text) for h in home.headings] == [(1, "Home")]
    assert home.metadata.description == "Home page" and home.metadata.canonical_url == "/"


# ---------------------------------------------------------------- 4. links still go through the existing discovery
def test_links_still_pass_through_existing_discovery(local_network):
    routes = {
        "/": doc("Home", "/a", "b", "/a#frag", "http://external.example.org/x", "mailto:me@example.com", "#top"),
        "/a": doc("A"),
        "/b": doc("B"),
    }
    with server(routes) as (base, _):
        result = crawl(base)
        home = result.pages[0]
        # parser layer: raw hrefs exactly as written, nothing resolved, filtered scheme-wise or scoped
        assert home.parsed.links == ("/a", "b", "/a#frag", "http://external.example.org/x", "mailto:me@example.com", "#top")
        # crawler layer: unchanged -- normalised, de-duplicated, in scope only
        assert result.queued == [base + "/", base + "/a", base + "/b"]
        assert (home.links_found, home.links_queued) == (4, 2)  # extract_links: no mailto/#top, /a#frag kept as a candidate
    assert result.rejections  # the external link was rejected by the URL engine, not by the parser


# ---------------------------------------------------------------- 5. non-HTML / failed pages carry no parse result
def test_non_html_and_failed_pages_have_no_parsed_page(local_network):
    routes = {
        "/": doc("Home", "/data", "/missing", "/text"),
        "/data": (200, b'{"title": "<title>json</title>"}', {"Content-Type": "application/json"}),
        "/text": (200, b"<title>plain</title>", {"Content-Type": "text/plain"}),
        "/missing": (404, b"<title>nope</title>", HTML_HEADERS),
    }
    with server(routes) as (base, _):
        result = crawl(base)
    parsed = {p.url.removeprefix(base): p.parsed for p in result.pages}
    assert parsed["/"] is not None and parsed["/"].title == "Home"
    assert parsed["/data"] is None and parsed["/text"] is None and parsed["/missing"] is None
    assert not result.pages[1].ok  # unsupported types / errors are still reported as before


def test_empty_html_body_gives_no_parsed_page_but_is_still_a_fetched_page(local_network):
    with server({"/": (200, b"", HTML_HEADERS)}) as (base, _):
        result = crawl(base)
    assert len(result.fetched) == 1 and result.pages[0].parsed is None


# ---------------------------------------------------------------- parser failure must not crash or alter the crawl
def test_parser_failure_does_not_break_the_crawl(local_network, monkeypatch, caplog):
    import app.crawler.bfs as bfs

    def boom(_result):
        raise RuntimeError("parser exploded")

    routes = {"/": doc("Home", "/a"), "/a": doc("A", "/b"), "/b": doc("B")}
    with server(routes) as (base, state):
        expected = crawl(base)  # reference run with the real parser
        monkeypatch.setattr(bfs, "parse_response", boom)
        with caplog.at_level(logging.WARNING):
            result = crawl(base)
    assert result.stop_reason is StopReason.COMPLETED
    assert result.visited == expected.visited and result.queued == expected.queued
    assert [(p.links_found, p.links_queued) for p in result.pages] == [(p.links_found, p.links_queued) for p in expected.pages]
    assert all(p.parsed is None and p.ok for p in result.pages)
    assert sum(1 for r in caplog.records if r.getMessage() == "html_parse_failed") == 3


def test_parser_failure_is_not_a_failed_crawl_in_the_database(local_network, session, monkeypatch):
    import app.crawler.bfs as bfs

    monkeypatch.setattr(bfs, "parse_response", lambda _r: (_ for _ in ()).throw(ValueError("bad")))
    crawl_row = new_crawl(session)
    with server({"/": doc("Home", "/a"), "/a": doc("A")}) as (base, _):
        result = crawl_into(session, base, crawl_row.id)
        stored = rows(session, crawl_row.id)
    assert result.stop_reason is StopReason.COMPLETED
    assert {p.status for p in stored.values()} == {PageStatus.FETCHED}
    assert all(p.title is None for p in stored.values())


# ---------------------------------------------------------------- 6. persistence and resume unchanged
def test_visited_page_persistence_still_works_and_stores_the_title(local_network, session):
    routes = {"/": doc("Home", "/a", "/bad"), "/a": doc("Page A"), "/bad": (500, b"<title>err</title>", HTML_HEADERS)}
    crawl_row = new_crawl(session)
    with server(routes) as (base, _):
        result = crawl_into(session, base, crawl_row.id)
        stored = rows(session, crawl_row.id)
    assert len(result.pages) == 3 and len(stored) == 3
    assert stored[base + "/"].status is PageStatus.FETCHED and stored[base + "/"].title == "Home"
    assert stored[base + "/a"].title == "Page A"
    assert stored[base + "/bad"].status is PageStatus.FAILED and stored[base + "/bad"].title is None


def test_resume_behaviour_is_unchanged(local_network, session):
    routes = {
        "/": doc("Home", "/a", "/b", "/c"),
        "/a": doc("A", "/d"),
        "/b": doc("B"),
        "/c": doc("C"),
        "/d": doc("D"),
    }
    crawl_row = new_crawl(session)
    with server(routes) as (base, state):
        first = crawl_into(session, base, crawl_row.id, max_pages=2)
        requested_first = list(state.requests)
        second = crawl_into(session, base, crawl_row.id, max_pages=10)
        stored = rows(session, crawl_row.id)
        requested_all = list(state.requests)
    assert first.stop_reason is StopReason.MAX_PAGES and len(first.pages) == 2
    assert second.stop_reason is StopReason.COMPLETED
    assert requested_all[: len(requested_first)] == requested_first
    assert len(requested_all) == len(set(requested_all)) == 5  # nothing requested twice across runs
    assert set(second.previously_visited) == {base + "/", base + "/a"}
    assert all(p.parsed is not None for p in second.pages)  # resumed runs parse as well
    assert {p.title for p in stored.values()} == {"Home", "A", "B", "C", "D"}  # earlier + resumed pages


# ---------------------------------------------------------------- encoding over the wire
def test_persian_page_over_http_is_parsed_and_stored_intact(local_network, session):
    body = '<html><head><title>صفحه اصلی | Home</title></head><body><h1>سلام دنیا</h1><p>می\u200cخواهم</p></body></html>'
    crawl_row = new_crawl(session)
    with server({"/": (200, body.encode("utf-8"), PERSIAN_HEADERS)}) as (base, _):
        result = crawl_into(session, base, crawl_row.id)
        stored = rows(session, crawl_row.id)
    parsed = result.pages[0].parsed
    assert parsed.title == "صفحه اصلی | Home" and parsed.headings[0].text == "سلام دنیا"
    assert "می\u200cخواهم" in parsed.text
    assert stored[base + "/"].title == "صفحه اصلی | Home"


def test_windows_1256_declared_in_http_header(local_network):
    body = "<html><head><title>سلام</title></head><body><p>دنيا</p></body></html>".encode("cp1256")  # cp1256 has Arabic yeh, not Persian ی
    headers = {"Content-Type": "text/html; charset=windows-1256"}
    with server({"/": (200, body, headers)}) as (base, _):
        result = crawl(base)
    assert result.pages[0].parsed.title == "سلام" and result.pages[0].parsed.text == "دنيا"
