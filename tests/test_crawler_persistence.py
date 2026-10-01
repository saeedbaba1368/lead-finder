"""Phase 6.3.2.2.1: persistence of page fetch metadata (BfsCrawler hook + PageRepository).

Crawler tests use the loopback test server from the BFS tests (no internet).
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from sqlalchemy import func, select

from app.models import Page, PageOutcome, PageStatus
from app.repositories import CrawlRepository, DomainRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler, page  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML, HTML_HEADERS, server


# ---------------------------------------------------------------- helpers
def new_crawl(session, seed="http://127.0.0.1"):
    domain, _ = DomainRepository(session).get_or_create("127.0.0.1")
    return CrawlRepository(session).create(domain.id, seed_url=seed)


def crawl_into(session, base, crawl_id, seed=None, **kwargs):
    async def main():
        async with make_crawler(base, page_repository=PageRepository(session), **kwargs) as crawler:
            return await crawler.crawl(seed or base + "/", crawl_id=crawl_id)

    return asyncio.run(main())


def rows(session, crawl_id):
    stmt = select(Page).where(Page.crawl_id == crawl_id).order_by(Page.id)
    return list(session.scalars(stmt))


def by_path(pages, base):
    return {p.url.removeprefix(base): p for p in pages}


def fetched_for(result, suffix):
    return next(p.result for p in result.pages if p.url.endswith(suffix))


def dead_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


# ---------------------------------------------------------------- 1. successful persistence
def test_successful_page_persistence(local_network, session):
    crawl = new_crawl(session)
    with server({"/": page("/a"), "/a": page()}) as (base, _):
        result = crawl_into(session, base, crawl.id)
        stored = by_path(rows(session, crawl.id), base)
    assert len(result.pages) == 2 and set(stored) == {"/", "/a"}
    for row, depth in ((stored["/"], 0), (stored["/a"], 1)):
        assert row.status is PageStatus.FETCHED
        assert row.http_status == 200
        assert row.content_type == "text/html"
        assert row.depth == depth
        assert row.fetched_at is not None
        assert row.error_message is None


# ---------------------------------------------------------------- 2. HTTP failure persistence
@pytest.mark.parametrize("code", [404, 500])
def test_http_failure_persistence(local_network, session, code):
    routes = {"/": page("/bad"), "/bad": (code, b"err", HTML_HEADERS)}
    crawl = new_crawl(session)
    with server(routes) as (base, _):
        crawl_into(session, base, crawl.id)
        stored = by_path(rows(session, crawl.id), base)
    bad = stored["/bad"]
    assert bad.status is PageStatus.FAILED
    assert bad.http_status == code
    assert bad.error_message
    assert bad.fetched_at is None
    assert stored["/"].status is PageStatus.FETCHED  # other pages unaffected


# ---------------------------------------------------------------- 3. network/request failure
def test_network_failure_persistence(local_network, session):
    port = dead_port()
    base = f"http://127.0.0.1:{port}"
    crawl = new_crawl(session)
    result = crawl_into(session, base, crawl.id)
    (row,) = rows(session, crawl.id)
    assert len(result.pages) == 1
    assert row.url == base + "/"
    assert row.status is PageStatus.FAILED
    assert row.http_status is None  # no HTTP response at all
    assert row.error_message
    assert row.outcome is PageOutcome.CONNECTION_ERROR
    assert row.redirect_count == 0


# ---------------------------------------------------------------- 4. final_url
def test_final_url_persistence(local_network, session):
    routes = {
        "/": page("/old", "/plain"),
        "/old": (301, b"", {"Location": "/new"}),
        "/new": (200, HTML, HTML_HEADERS),
        "/plain": (200, HTML, HTML_HEADERS),
    }
    crawl = new_crawl(session)
    with server(routes) as (base, _):
        crawl_into(session, base, crawl.id)
        stored = by_path(rows(session, crawl.id), base)
    assert stored["/old"].url == base + "/old"
    assert stored["/old"].final_url == base + "/new"
    assert stored["/plain"].final_url == base + "/plain"  # no redirect: final == requested


# ---------------------------------------------------------------- 5. charset
def test_charset_persistence(local_network, session):
    routes = {
        "/": page("/latin"),
        "/latin": (200, HTML, {"Content-Type": "text/html; charset=iso-8859-1"}),
    }
    crawl = new_crawl(session)
    with server(routes) as (base, _):
        crawl_into(session, base, crawl.id)
        stored = by_path(rows(session, crawl.id), base)
    assert stored["/"].charset == "utf-8"
    assert stored["/latin"].charset == "iso-8859-1"


# ---------------------------------------------------------------- 6. response size
def test_response_size_persistence(local_network, session):
    crawl = new_crawl(session)
    with server({"/": (200, HTML, HTML_HEADERS)}) as (base, _):
        result = crawl_into(session, base, crawl.id)
        (row,) = rows(session, crawl.id)
    assert row.response_size == len(HTML) == result.pages[0].result.size


# ---------------------------------------------------------------- 7. duration
def test_duration_persistence(local_network, session):
    crawl = new_crawl(session)
    with server({"/": (200, HTML, HTML_HEADERS)}) as (base, _):
        result = crawl_into(session, base, crawl.id)
        (row,) = rows(session, crawl.id)
    assert row.duration_seconds is not None and row.duration_seconds >= 0
    assert row.duration_seconds == pytest.approx(result.pages[0].result.duration)


# ---------------------------------------------------------------- 8. redirect metadata
def test_redirect_metadata_persistence(local_network, session):
    routes = {
        "/": page("/a", "/plain"),
        "/a": (301, b"", {"Location": "/b"}),
        "/b": (302, b"", {"Location": "/c"}),
        "/c": (200, HTML, HTML_HEADERS),
        "/plain": (200, HTML, HTML_HEADERS),
    }
    crawl = new_crawl(session)
    with server(routes) as (base, _):
        crawl_into(session, base, crawl.id)
        stored = by_path(rows(session, crawl.id), base)
    row = stored["/a"]
    assert row.redirect_count == 2
    assert row.redirect_chain == [
        {"url": base + "/a", "status_code": 301, "location": base + "/b"},
        {"url": base + "/b", "status_code": 302, "location": base + "/c"},
    ]
    assert row.final_url == base + "/c"
    assert stored["/plain"].redirect_count == 0
    assert stored["/plain"].redirect_chain == []


# ---------------------------------------------------------------- 9. outcome / failure kind
def test_outcome_persistence(local_network, session):
    routes = {
        "/": page("/ok", "/missing", "/img"),
        "/ok": (200, HTML, HTML_HEADERS),
        "/missing": (404, b"x", HTML_HEADERS),
        "/img": (200, b"\x89PNG", {"Content-Type": "image/png"}),
    }
    crawl = new_crawl(session)
    with server(routes) as (base, _):
        crawl_into(session, base, crawl.id)
        stored = by_path(rows(session, crawl.id), base)
    assert stored["/ok"].outcome is PageOutcome.OK
    assert stored["/missing"].outcome is PageOutcome.HTTP_ERROR
    assert stored["/img"].outcome is PageOutcome.UNSUPPORTED_CONTENT_TYPE
    assert stored["/img"].status is PageStatus.FAILED


# ---------------------------------------------------------------- 10. duplicate URL prevention
def test_recrawl_in_same_crawl_does_not_duplicate_pages(local_network, session):
    crawl = new_crawl(session)
    with server({"/": page("/a"), "/a": page()}) as (base, _):
        crawl_into(session, base, crawl.id)
        first = {p.url: p.id for p in rows(session, crawl.id)}
        crawl_into(session, base, crawl.id)
        second = {p.url: p.id for p in rows(session, crawl.id)}
    assert len(first) == 2
    assert second == first  # same rows updated, none added


def test_repository_get_or_create_is_idempotent(session):
    crawl = new_crawl(session)
    repo = PageRepository(session)
    a, created_a = repo.get_or_create(crawl.id, "https://example.com/x")
    b, created_b = repo.get_or_create(crawl.id, "https://example.com/x")
    assert created_a and not created_b and a.id == b.id
    assert repo.count_for_crawl(crawl.id) == 1


# ---------------------------------------------------------------- 11. normalized-equivalent duplicates
def test_normalized_equivalent_urls_are_stored_once(local_network, session):
    routes = {
        "/": page("/a", "/a/", "/a#frag", "/a?utm_source=news", "./a", "b"),
        "/a": page(),
        "/b": page("/a"),
    }
    crawl = new_crawl(session)
    with server(routes) as (base, state):
        crawl_into(session, base, crawl.id)
        stored = rows(session, crawl.id)
    assert sorted(p.url.removeprefix(base) for p in stored) == ["/", "/a", "/b"]
    assert len({p.url_hash for p in stored}) == len(stored)
    assert len({p.dedupe_key for p in stored}) == len(stored)


# ---------------------------------------------------------------- 12. same URL, different crawls
def test_same_url_in_different_crawls_gets_separate_rows(local_network, session):
    first, second = new_crawl(session), new_crawl(session)
    with server({"/": page()}) as (base, _):
        crawl_into(session, base, first.id)
        crawl_into(session, base, second.id)
        a, b = rows(session, first.id), rows(session, second.id)
    assert len(a) == len(b) == 1
    assert a[0].id != b[0].id
    assert a[0].url == b[0].url and a[0].url_hash == b[0].url_hash
    assert a[0].dedupe_key == b[0].dedupe_key
    assert session.scalar(select(func.count()).select_from(Page)) == 2


# ---------------------------------------------------------------- repository-level / guard
def test_repository_mark_methods_store_metadata(session):
    crawl = new_crawl(session)
    repo = PageRepository(session)
    ok = repo.create(crawl.id, "https://example.com/ok", dedupe_key="k1")
    assert ok.dedupe_key == "k1" and ok.redirect_count == 0 and ok.outcome is None
    chain = [{"url": "https://example.com/ok", "status_code": 302, "location": "https://example.com/z"}]
    repo.mark_fetched(
        ok, http_status=200, final_url="https://example.com/z", charset="utf-8", response_size=10,
        duration_seconds=0.5, redirect_chain=chain, dedupe_key="k2",
    )
    assert (ok.final_url, ok.charset, ok.response_size, ok.duration_seconds) == (
        "https://example.com/z", "utf-8", 10, 0.5)
    assert ok.redirect_count == 1 and ok.redirect_chain == chain  # count derived from the chain
    assert ok.outcome is PageOutcome.OK and ok.dedupe_key == "k2"

    bad = repo.create(crawl.id, "https://example.com/bad")
    repo.mark_failed(bad, "boom", outcome=PageOutcome.TIMEOUT, duration_seconds=1.5)
    assert bad.status is PageStatus.FAILED and bad.outcome is PageOutcome.TIMEOUT
    assert bad.duration_seconds == 1.5 and bad.redirect_count == 0  # unsupplied values left unchanged


def test_crawler_requires_crawl_id_with_repository(session):
    async def main():
        async with make_crawler("http://127.0.0.1", page_repository=PageRepository(session)) as crawler:
            await crawler.crawl("http://127.0.0.1/")

    with pytest.raises(ValueError, match="crawl_id"):
        asyncio.run(main())
