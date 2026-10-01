"""BFS crawler against a local loopback site (no internet). Async code runs via `asyncio.run`."""

from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlsplit

import pytest

from app.core.config import Settings
from app.crawler import (
    AsyncHttpClient, BfsCrawler, FetchOutcome, StopReason, build_crawler,
)
from app.urls import UrlEvaluator, UrlPolicy
from tests.test_crawler_http_client import (
    _REAL_CONNECT, _REAL_CONNECT_EX, _REAL_GETADDRINFO, HTML_HEADERS, server,
)


@pytest.fixture
def local_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket.socket, "connect_ex", _REAL_CONNECT_EX)
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)


def page(*hrefs: str, title: str = "t") -> tuple[int, bytes, dict[str, str]]:
    links = "".join(f'<a href="{h}">link</a>' for h in hrefs)
    return 200, f"<html><head><title>{title}</title></head><body>{links}</body></html>".encode(), HTML_HEADERS


def site(base: str, port: int) -> dict:
    """/ -> a, b, c;  a -> a/x, /;  b -> b/y, a;  c -> a/x;  a/x -> a/x/z (depths 0,1,1,1,2,2,3)."""
    return {
        "/": page(
            "/a", "b", f"http://127.0.0.1:{port}/c",              # root-relative, relative, absolute
            "/a#frag", "/a?utm_source=news", "/a/", "./a",        # all duplicates of /a
            "http://external.example.org/x", f"http://127.0.0.2:{port}/x",   # external
            "mailto:me@example.com", "#top", "http://[bad", "/doc.pdf",      # not crawlable
        ),
        "/a": page("/a/x", "/"),
        "/b": page("/b/y", "/a"),
        "/c": page("/a/x"),
        "/a/x": page("/a/x/z"),
        "/b/y": page(),
        "/a/x/z": page(),
    }


def make_crawler(base: str, **kwargs) -> BfsCrawler:
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy, seed_url=base)
    client = AsyncHttpClient(evaluator=evaluator, resolver=lambda host: ["127.0.0.1"])
    return BfsCrawler(client, evaluator, **kwargs)


def crawl(base: str, seed: str | None = None, **kwargs):
    async def main():
        async with make_crawler(base, **kwargs) as crawler:
            return await crawler.crawl(seed or base + "/")

    return asyncio.run(main())


def paths(urls) -> list[str]:
    out = []
    for u in urls:
        p = urlsplit(u)
        out.append(p.path + (f"?{p.query}" if p.query else ""))
    return out


# The server helper takes its routes at creation; build them lazily once the port is known.
def serve_site(extra=None):
    from contextlib import contextmanager

    @contextmanager
    def ctx():
        routes: dict = {}
        with server(routes) as (base, state):
            port = int(base.rsplit(":", 1)[1])
            routes.update(site(base, port))
            routes.update(extra or {})
            yield base, state, routes

    return ctx()


# --- discovery, normalisation, duplicates, external links ------------------------------------

def test_internal_link_discovery_and_normalisation(local_network):
    with serve_site() as (base, state, _):
        result = crawl(base)
    assert paths(result.visited) == ["/", "/a", "/b", "/c", "/a/x", "/b/y", "/a/x/z"]
    assert state.requests == ["/", "/a", "/b", "/c", "/a/x", "/b/y", "/a/x/z"]  # each exactly once
    # /a was linked 5 ways (fragment, tracking param, trailing slash, ./a) but queued once
    assert paths(result.queued).count("/a") == 1
    assert result.rejections["duplicate"] >= 4


def test_external_and_non_crawlable_links_are_excluded(local_network):
    with serve_site() as (base, state, _):
        result = crawl(base)
    assert "/doc.pdf" not in state.requests and "/x" not in state.requests
    assert all(urlsplit(u).hostname == "127.0.0.1" for u in result.queued)
    assert result.rejections["out_of_scope"] >= 2
    assert result.rejections["non_html_extension"] >= 1
    assert result.rejections["invalid_url"] + result.rejections["invalid_host"] >= 1


def test_seed_outside_scope_or_ineligible_is_rejected_without_requests(local_network):
    with serve_site() as (base, state, _):
        external = crawl(base, seed="http://external.example.org/")
        pdf = crawl(base, seed=base + "/doc.pdf")
    assert external.stop_reason is StopReason.SEED_REJECTED and external.pages == []
    assert pdf.stop_reason is StopReason.SEED_REJECTED and pdf.seed_rejection
    assert state.requests == []


# --- BFS ordering, depth, limits -------------------------------------------------------------

def test_bfs_is_level_by_level(local_network):
    with serve_site() as (base, _, _):
        result = crawl(base, max_depth=10, max_pages=100)
    depths = [p.depth for p in result.pages]
    assert depths == [0, 1, 1, 1, 2, 2, 3]
    assert depths == sorted(depths)
    assert result.pages[0].parent is None
    assert paths([p.parent for p in result.pages[1:]]) == ["/", "/", "/", "/a", "/b", "/a/x"]
    assert result.current_depth == 3 and result.deepest_level == 3
    assert result.stop_reason is StopReason.COMPLETED and not result.depth_limited
    assert len(result.fetched) == 7 and result.failed == [] and result.pending == []


@pytest.mark.parametrize(
    ("max_depth", "expected"),
    [
        (0, ["/"]),
        (1, ["/", "/a", "/b", "/c"]),
        (2, ["/", "/a", "/b", "/c", "/a/x", "/b/y"]),
    ],
)
def test_max_depth(local_network, max_depth, expected):
    with serve_site() as (base, state, _):
        result = crawl(base, max_depth=max_depth)
    assert paths(result.visited) == expected
    assert state.requests == expected  # nothing beyond the limit was requested
    assert result.depth_limited and result.skipped_by_depth > 0
    assert result.stop_reason is StopReason.COMPLETED  # a clean stop, not an error
    assert result.deepest_level == max_depth


def test_max_pages_stops_cleanly_mid_level(local_network):
    with serve_site() as (base, state, _):
        result = crawl(base, max_pages=3, max_depth=10)
    assert paths(result.visited) == ["/", "/a", "/b"]
    assert state.requests == ["/", "/a", "/b"]
    assert result.stop_reason is StopReason.MAX_PAGES
    assert len(result.queued) <= 3 and result.skipped_by_max_pages > 0


def test_max_pages_equal_to_site_size_is_completed(local_network):
    with serve_site() as (base, _, _):
        result = crawl(base, max_pages=7, max_depth=10)
    assert len(result.visited) == 7 and result.stop_reason is StopReason.COMPLETED


def test_max_pages_one_fetches_only_the_seed(local_network):
    with serve_site() as (base, state, _):
        result = crawl(base, max_pages=1)
    assert state.requests == ["/"] and result.stop_reason is StopReason.MAX_PAGES


# --- cycles, failures, redirects -------------------------------------------------------------

def test_cyclic_links_terminate_and_fetch_each_page_once(local_network):
    extra = {
        "/": page("/a"), "/a": page("/b", "/a", "/"), "/b": page("/a", "/", "/b"),
    }
    with serve_site(extra) as (base, state, _):
        result = crawl(base, max_depth=50, max_pages=50)
    assert state.requests == ["/", "/a", "/b"]
    assert result.stop_reason is StopReason.COMPLETED


def test_failed_pages_do_not_stop_the_crawl(local_network):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    extra = {
        "/": page("/missing", "/boom", "/pdf-page", "/slow-ok", f"http://127.0.0.1:{dead_port}/dead"),
        "/boom": (500, b"err", HTML_HEADERS),
        "/pdf-page": (200, b"%PDF", {"Content-Type": "application/pdf"}),
        "/slow-ok": page("/after"),
        "/after": page(),
    }
    with serve_site(extra) as (base, state, _):
        result = crawl(base)
    outcomes = {urlsplit(p.url).path: p.result.outcome for p in result.pages}
    assert outcomes["/missing"] is FetchOutcome.HTTP_ERROR
    assert outcomes["/boom"] is FetchOutcome.HTTP_ERROR
    assert outcomes["/pdf-page"] is FetchOutcome.UNSUPPORTED_CONTENT_TYPE
    assert outcomes["/dead"] is FetchOutcome.CONNECTION_ERROR
    assert outcomes["/slow-ok"] is FetchOutcome.OK and outcomes["/after"] is FetchOutcome.OK  # BFS went on
    assert sorted(paths(result.failed)) == ["/boom", "/dead", "/missing", "/pdf-page"]
    assert sorted(paths(result.fetched)) == ["/", "/after", "/slow-ok"]
    assert len(result.visited) == len(result.fetched) + len(result.failed)


def test_failed_pages_count_toward_max_pages(local_network):
    extra = {"/": page("/m1", "/m2", "/m3", "/ok")}
    with serve_site(extra) as (base, state, _):
        result = crawl(base, max_pages=3)
    assert len(result.visited) == 3 and result.stop_reason is StopReason.MAX_PAGES


def test_redirect_target_is_not_fetched_twice(local_network):
    extra = {
        "/": page("/r", "/a"),
        "/r": (302, b"", {"Location": "/a"}),
        "/a": page("/b"),
        "/b": page(),
    }
    with serve_site(extra) as (base, state, _):
        result = crawl(base)
    assert state.requests == ["/", "/r", "/a", "/b"]
    rpage = next(p for p in result.pages if urlsplit(p.url).path == "/r")
    assert rpage.ok and urlsplit(rpage.result.final_url).path == "/a" and rpage.result.redirected
    assert paths(result.redirect_duplicates) == ["/a"] and result.pending == []


def test_redirect_to_external_host_is_blocked(local_network):
    extra = {"/": page("/out"), "/out": (302, b"", {"Location": "http://external.example.org/x"})}
    with serve_site(extra) as (base, _, _):
        result = crawl(base)
    out = next(p for p in result.pages if urlsplit(p.url).path == "/out")
    assert out.result.outcome is FetchOutcome.BLOCKED and result.failed == [out.url]


def test_relative_links_use_base_href_and_page_directory(local_network):
    extra = {
        "/": page("/dir/index"),
        "/dir/index": (200, b'<a href="sibling">s</a><a href="../up">u</a>', HTML_HEADERS),
        "/dir/sibling": page(), "/up": page(),
    }
    with serve_site(extra) as (base, state, _):
        result = crawl(base)
    assert paths(result.visited) == ["/", "/dir/index", "/dir/sibling", "/up"]


def test_malformed_html_is_still_crawled(local_network):
    extra = {"/": (200, b"<html><a href=/ok <div><a href='/second'>x</a><p><<<", HTML_HEADERS),
             "/second": page(), "/ok": page()}
    with serve_site(extra) as (base, _, _):
        result = crawl(base)
    assert "/second" in paths(result.visited)


def test_query_string_links_are_kept_distinct_but_sorted(local_network):
    extra = {"/": page("/s?b=2&a=1", "/s?a=1&b=2", "/s?a=1&b=2&utm_campaign=x"), "/s": page()}
    with serve_site(extra) as (base, state, _):
        result = crawl(base)
    assert paths(result.visited) == ["/", "/s?a=1&b=2"]
    assert len(state.requests) == 2


# --- configuration / construction ------------------------------------------------------------

def test_invalid_limits_are_rejected():
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy, seed_url="http://127.0.0.1:1")

    async def main():
        async with AsyncHttpClient(evaluator=evaluator) as client:
            with pytest.raises(ValueError):
                BfsCrawler(client, evaluator, max_pages=0)
            with pytest.raises(ValueError):
                BfsCrawler(client, evaluator, max_depth=-1)

    asyncio.run(main())


def test_settings_defaults_validation_and_build_crawler(local_network):
    from pydantic import ValidationError

    s = Settings(_env_file=None)
    assert s.crawler_max_pages == 100 and s.crawler_max_depth == 3
    for bad in ({"crawler_max_pages": 0}, {"crawler_max_depth": -1}, {"crawler_max_depth": 51}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **bad)

    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    limited = Settings(_env_file=None, crawler_max_pages=2, crawler_max_depth=1)
    with serve_site() as (base, state, _):
        async def main():
            async with build_crawler(
                limited, base, policy=policy, resolver=lambda h: ["127.0.0.1"]
            ) as crawler:
                assert (crawler.max_pages, crawler.max_depth) == (2, 1)
                return await crawler.crawl(base + "/")

        result = asyncio.run(main())
    assert paths(result.visited) == ["/", "/a"] and result.stop_reason is StopReason.MAX_PAGES


def test_crawler_can_be_reused_with_fresh_state(local_network):
    with serve_site() as (base, state, _):
        async def main():
            async with make_crawler(base, max_depth=1) as crawler:
                return await crawler.crawl(base + "/"), await crawler.crawl(base + "/")

        first, second = asyncio.run(main())
    assert paths(first.visited) == paths(second.visited) == ["/", "/a", "/b", "/c"]
