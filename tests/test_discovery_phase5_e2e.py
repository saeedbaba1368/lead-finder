"""Phase 5.4: comprehensive deterministic end-to-end discovery tests.

One loopback server (127.0.0.1, ephemeral port, no DNS, no external services) serves the whole
tree below and counts every request, so the tests can prove *exactly* which URLs were fetched:

    /robots.txt
      Sitemap: /root-index.xml (twice), /cycle-a.xml, /unknown.xml, https://evil.example.org/x.xml
    /root-index.xml      index -> products-index, blog, malformed, blog (dup), products-index (dup)
    /products-index.xml  index -> products-2026, root-index (cycle back to the top)
    /products-2026.xml   index -> products-2026-01, products-2026-02
    /products-2026-01.xml  urlset          /products-2026-02.xml  urlset (one page duplicated)
    /blog.xml            urlset (pdf, off-domain page, duplicate page, entry without <loc>, empty <loc>)
    /malformed.xml       broken XML
    /cycle-a.xml <-> /cycle-b.xml   index cycle (cycle-b also references blog again)
    /unknown.xml         <rss> root
"""

from __future__ import annotations

import threading
from collections import Counter

from app.discovery import FetchResult, FetchStatus, HttpFetcher, SitemapDiscovery
from app.discovery.sitemap import SitemapParseStatus as S
from app.discovery.sitemap import _Collector, _make_parser, _UnsafeXml, parse_sitemap
from app.urls import RejectReason, UrlEvaluator, UrlPolicy
from tests.test_discovery_http import (
    local_network,  # noqa: F401
    server,
)

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
B = "http://127.0.0.1:{port}"  # the test server substitutes {port} in every body


def urlset(*locs: str, raw: str = "") -> bytes:
    body = "".join(f"<url><loc>{loc if '//' in loc else B + loc}</loc></url>" for loc in locs)
    return f"<urlset {NS}>{body}{raw}</urlset>".encode()


def index(*paths: str) -> bytes:
    body = "".join(f"<sitemap><loc>{p if '//' in p else B + p}</loc></sitemap>" for p in paths)
    return f"<sitemapindex {NS}>{body}</sitemapindex>".encode()


def ok(body: bytes) -> tuple[int, bytes, dict[str, str]]:
    return (200, body, {"Content-Type": "application/xml"})


class CountingRoutes(dict):
    """Route table that records every path the server is asked for."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hits: Counter[str] = Counter()
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            self.hits[key] += 1
        return super().get(key, default)


def site_routes(**overrides) -> CountingRoutes:
    routes = {
        "/robots.txt": ok(
            (
                "User-agent: *\nDisallow: /admin/\n"
                f"Sitemap: {B}/root-index.xml\n"
                f"Sitemap: {B}/cycle-a.xml\n"
                f"Sitemap: {B}/unknown.xml\n"
                f"Sitemap: HTTP://127.0.0.1:{{port}}/root-index.xml#again\n"  # same sitemap after normalisation
                "Sitemap: https://evil.example.org/x.xml\n"  # out of scope: never fetched
                "Sitemap: not a url\n"
            ).encode()
        ),
        "/root-index.xml": ok(
            index(
                "/products-index.xml", "/blog.xml", "/malformed.xml", "/blog.xml",
                "/products-index.xml",
            )
        ),
        "/products-index.xml": ok(index("/products-2026.xml", "/root-index.xml")),
        "/products-2026.xml": ok(index("/products-2026-01.xml", "/products-2026-02.xml")),
        "/products-2026-01.xml": ok(urlset("/products/2026/01/a", "/products/2026/01/b")),
        "/products-2026-02.xml": ok(urlset("/products/2026/02/a", "/products/2026/01/a")),
        "/blog.xml": ok(
            urlset(
                "/blog/a", "/blog/b", "/docs/manual.pdf", "https://evil.example.org/p",
                "/products/2026/01/b",
                raw="<url><lastmod>2026-01-01</lastmod></url><url><loc>  </loc></url>",
            )
        ),
        "/malformed.xml": ok(b"<urlset><url><loc>http://x"),
        "/cycle-a.xml": ok(index("/cycle-b.xml")),
        "/cycle-b.xml": ok(index("/cycle-a.xml", "/blog.xml")),
        "/unknown.xml": ok(b"<rss><channel><item>x</item></channel></rss>"),
    }
    routes.update(overrides)
    return CountingRoutes(routes)


def discover(port: int, **limits):
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy, seed_url=f"http://127.0.0.1:{port}")
    fetcher = HttpFetcher(evaluator, resolver=lambda host: ["127.0.0.1"], timeout=5)
    return SitemapDiscovery(evaluator, fetcher, **limits).discover(f"http://127.0.0.1:{port}")


def name(url: str) -> str:
    return url.rsplit("/", 1)[1]


# --------------------------------------------------------------------------- full flow


def test_full_discovery_flow_and_exact_request_set(local_network):  # noqa: F811
    routes = site_routes()
    with server(routes) as port:
        r = discover(port)
    base = f"http://127.0.0.1:{port}"

    # pages: depth-first, document order, policy-filtered, de-duplicated
    assert r.urls == [
        f"{base}/products/2026/01/a", f"{base}/products/2026/01/b",
        f"{base}/products/2026/02/a", f"{base}/blog/a", f"{base}/blog/b",
    ]
    assert not r.used_fallback and r.error is None
    assert r.robots_status is FetchStatus.OK

    # network efficiency: every reachable file exactly once, nothing else
    assert dict(routes.hits) == {
        "/robots.txt": 1, "/root-index.xml": 1, "/products-index.xml": 1, "/products-2026.xml": 1,
        "/products-2026-01.xml": 1, "/products-2026-02.xml": 1, "/blog.xml": 1,
        "/malformed.xml": 1, "/cycle-a.xml": 1, "/cycle-b.xml": 1, "/unknown.xml": 1,
    }
    assert "/sitemap.xml" not in routes.hits  # declarations existed: no fallback request

    # traversal accounting
    assert len(r.sitemaps) == 10
    assert [name(o.url) for o in r.sitemaps] == [
        "root-index.xml", "products-index.xml", "products-2026.xml", "products-2026-01.xml",
        "products-2026-02.xml", "blog.xml", "malformed.xml", "cycle-a.xml", "cycle-b.xml",
        "unknown.xml",
    ]
    assert r.max_depth_seen == 3
    assert r.duplicate_sitemap_refs == 5  # blog, products-index, root-index, cycle-a, blog
    assert not (r.depth_limit_reached or r.sitemap_limit_reached or r.url_limit_reached)

    # isolation: only the two bad sitemaps failed, everything else was still processed
    assert {name(o.url): o.parse_status for o in r.failed_sitemaps} == {
        "malformed.xml": S.MALFORMED, "unknown.xml": S.UNSUPPORTED}
    by_name = {name(o.url): o for o in r.sitemaps}
    assert by_name["unknown.xml"].detail == "root:rss"
    assert by_name["blog.xml"].missing_locs == 1 and by_name["blog.xml"].empty_locs == 1

    # policy: evil declaration + evil page, pdf, duplicates, invalid declaration
    assert r.rejections[RejectReason.OUT_OF_SCOPE] == 2
    assert r.rejections[RejectReason.NON_HTML_EXTENSION] == 1
    assert r.rejections[RejectReason.DUPLICATE] == 2
    assert r.rejections[RejectReason.INVALID_URL] == 0  # "not a url" never becomes a declaration


def test_fallback_flow_fetches_robots_and_sitemap_xml_once(local_network):  # noqa: F811
    routes = CountingRoutes({"/sitemap.xml": ok(urlset("/a", "/b"))})
    with server(routes) as port:
        r = discover(port)
    assert r.used_fallback and r.robots_status is FetchStatus.NOT_FOUND
    assert r.urls == [f"http://127.0.0.1:{port}/a", f"http://127.0.0.1:{port}/b"]
    assert dict(routes.hits) == {"/robots.txt": 1, "/sitemap.xml": 1}


def test_fallback_is_not_used_when_robots_declares_a_sitemap(local_network):  # noqa: F811
    routes = CountingRoutes({
        "/robots.txt": ok(f"Sitemap: {B}/only.xml\n".encode()),
        "/only.xml": ok(urlset("/x")),
        "/sitemap.xml": ok(urlset("/should-not-be-read")),
    })
    with server(routes) as port:
        r = discover(port)
    assert r.urls == [f"http://127.0.0.1:{port}/x"]
    assert "/sitemap.xml" not in routes.hits


# --------------------------------------------------------------------------- limits


def test_depth_limit_stops_fetching_deeper_children(local_network):  # noqa: F811
    routes = site_routes()
    with server(routes) as port:
        r = discover(port, max_depth=1)
    assert r.depth_limit_reached and r.max_depth_seen == 1
    assert "/products-2026.xml" not in routes.hits  # depth 2 was never requested
    assert "/products-2026-01.xml" not in routes.hits


def test_sitemap_count_limit_bounds_requests(local_network):  # noqa: F811
    routes = site_routes()
    with server(routes) as port:
        r = discover(port, max_sitemaps=3)
    assert r.sitemap_limit_reached and len(r.sitemaps) == 3
    fetched = {p for p in routes.hits if p.endswith(".xml")}
    assert len(fetched) == 3  # robots.txt is not counted as a sitemap


def test_url_count_limit_stops_fetching_more_sitemaps(local_network):  # noqa: F811
    routes = site_routes()
    with server(routes) as port:
        r = discover(port, max_urls=2)
    assert r.url_limit_reached and len(r.urls) == 2
    assert "/blog.xml" not in routes.hits and "/cycle-a.xml" not in routes.hits


def test_oversized_sitemap_is_isolated(local_network):  # noqa: F811
    big = urlset(*[f"/p{i}" for i in range(400)])
    routes = CountingRoutes({
        "/robots.txt": ok(f"Sitemap: {B}/big.xml\nSitemap: {B}/small.xml\n".encode()),
        "/big.xml": ok(big),
        "/small.xml": ok(urlset("/ok")),
    })
    with server(routes) as port:
        policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
        ev = UrlEvaluator(policy, seed_url=f"http://127.0.0.1:{port}")
        fetcher = HttpFetcher(ev, resolver=lambda h: ["127.0.0.1"], max_bytes=2000)
        r = SitemapDiscovery(ev, fetcher, max_sitemap_bytes=2000).discover(f"http://127.0.0.1:{port}")
    assert r.urls == [f"http://127.0.0.1:{port}/ok"]
    assert {name(o.url): o.fetch_status for o in r.failed_sitemaps} == {
        "big.xml": FetchStatus.TOO_LARGE}


# --------------------------------------------------------------------------- HTTP errors


def test_http_errors_are_isolated_per_sitemap(local_network):  # noqa: F811
    codes = {403: FetchStatus.FORBIDDEN, 404: FetchStatus.NOT_FOUND, 429: FetchStatus.RATE_LIMITED,
             500: FetchStatus.SERVER_ERROR}
    routes = {f"/e{c}.xml": (c, b"", {}) for c in codes}
    routes["/robots.txt"] = ok(
        "".join(f"Sitemap: {B}/e{c}.xml\n" for c in codes).encode() + f"Sitemap: {B}/good.xml\n".encode()
    )
    routes["/good.xml"] = ok(urlset("/good"))
    with server(CountingRoutes(routes)) as port:
        r = discover(port)
    assert r.urls == [f"http://127.0.0.1:{port}/good"]
    assert {name(o.url): o.fetch_status for o in r.failed_sitemaps} == {
        f"e{c}.xml": s for c, s in codes.items()}


def test_connection_refused_is_reported_not_raised(local_network):  # noqa: F811
    with server({}) as port:
        pass  # server is closed: nothing listens on this port any more
    r = discover(port)
    assert r.robots_status in (FetchStatus.CONNECTION_ERROR, FetchStatus.TIMEOUT)
    assert r.urls == [] and r.error is None


# --------------------------------------------------------------------------- XML security


XXE = (
    b'<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY x SYSTEM "http://127.0.0.1:{port}/canary">]>'
    b"<urlset><url><loc>http://127.0.0.1:{port}/&x;</loc></url></urlset>"
)


def test_external_entity_is_never_resolved_over_http(local_network):  # noqa: F811
    routes = CountingRoutes({"/sitemap.xml": ok(XXE), "/canary": ok(b"secret")})
    with server(routes) as port:
        r = discover(port)
    assert r.urls == []
    assert r.sitemaps[0].parse_status is S.UNSAFE
    assert "/canary" not in routes.hits


def test_parser_itself_refuses_dtd_features_even_without_the_prefilter():
    """The byte pre-check is defence in depth; the expat handlers must hold on their own."""
    payloads = [
        b'<!DOCTYPE urlset [<!ENTITY x "y">]><urlset/>',
        b'<!DOCTYPE urlset SYSTEM "http://example.invalid/x.dtd"><urlset/>',
        b"<!DOCTYPE urlset><urlset/>",
    ]
    for payload in payloads:
        collector = _Collector(10)
        try:
            _make_parser(collector).Parse(payload, True)
        except _UnsafeXml:
            continue
        raise AssertionError(f"parser accepted a DTD: {payload!r}")


def test_billion_laughs_and_encoded_doctype_are_unsafe_or_malformed():
    laughs = (b'<!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
              b"<urlset><url><loc>&b;</loc></url></urlset>")
    utf16 = ('<?xml version="1.0" encoding="utf-16"?><!DOCTYPE urlset [<!ENTITY x "y">]><urlset/>'
             ).encode("utf-16")
    assert parse_sitemap(laughs).status is S.UNSAFE
    assert parse_sitemap(utf16).status in (S.UNSAFE, S.MALFORMED)


def test_deeply_nested_xml_is_handled_without_recursion_errors():
    deep = b"<urlset>" + b"<a>" * 50_000 + b"</a>" * 50_000 + b"</urlset>"
    assert parse_sitemap(deep, max_bytes=10_000_000).status is S.OK


# --------------------------------------------------------------------------- policy bypass


def test_rejected_urls_are_never_requested():
    """Fake fetcher: sitemap URLs the policy refuses must not reach the network layer."""
    calls: list[str] = []
    child_locs = (
        "https://evil.example.org/a.xml", "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd", "ftp://example.com/a.xml", "/relative.xml", "javascript:alert(1)",
        "http://10.0.0.5/internal.xml", "https://example.com/good.xml",
    )
    files = {
        "https://example.com/robots.txt": FetchResult("", FetchStatus.NOT_FOUND),
        "https://example.com/sitemap.xml": FetchResult(
            "", FetchStatus.OK, 200,
            ("<sitemapindex>" + "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in child_locs)
             + "</sitemapindex>").encode(),
        ),
        "https://example.com/good.xml": FetchResult(
            "", FetchStatus.OK, 200, b"<urlset><url><loc>https://example.com/p</loc></url></urlset>"),
    }

    def fetcher(url: str) -> FetchResult:
        calls.append(url)
        return files.get(url, FetchResult(url, FetchStatus.NOT_FOUND))

    ev = UrlEvaluator(UrlPolicy(), seed_url="https://example.com")
    r = SitemapDiscovery(ev, fetcher).discover("example.com")
    assert calls == [
        "https://example.com/robots.txt", "https://example.com/sitemap.xml",
        "https://example.com/good.xml",
    ]
    assert r.urls == ["https://example.com/p"]
    assert sum(r.rejections.values()) == 7


def test_duplicate_and_cyclic_references_never_refetch():
    calls: list[str] = []

    def fetcher(url: str) -> FetchResult:
        calls.append(url)
        idx = lambda *c: ("<sitemapindex>" + "".join(
            f"<sitemap><loc>{x}</loc></sitemap>" for x in c) + "</sitemapindex>").encode()
        body = {
            "https://example.com/sitemap.xml": idx(
                "https://example.com/a.xml", "https://EXAMPLE.com/a.xml#x",
                "https://example.com/sitemap.xml"),
            "https://example.com/a.xml": idx("https://example.com/b.xml"),
            "https://example.com/b.xml": idx("https://example.com/a.xml",
                                             "https://example.com/sitemap.xml"),
        }.get(url)
        if body is None:
            return FetchResult(url, FetchStatus.NOT_FOUND)
        return FetchResult(url, FetchStatus.OK, 200, body)

    ev = UrlEvaluator(UrlPolicy(), seed_url="https://example.com")
    r = SitemapDiscovery(ev, fetcher).discover("example.com")
    assert sorted(calls) == sorted(set(calls))  # no URL requested twice
    assert len(calls) == 4  # robots.txt + sitemap.xml + a.xml + b.xml
    assert r.duplicate_sitemap_refs == 4
