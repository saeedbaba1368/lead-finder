"""Phase 5.2 against a realistic sitemap tree served by a local loopback HTTP server:

/robots.txt /sitemap-index.xml /products.xml /blog.xml /news.xml /malformed.xml ...
"""

from __future__ import annotations

from app.discovery import FetchStatus, HttpFetcher, SitemapDiscovery
from app.discovery.sitemap import SitemapParseStatus as S
from app.urls import RejectReason, UrlEvaluator, UrlPolicy
from tests.test_discovery_http import (
    local_network,  # noqa: F401
    server,
)

NS = b'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
B = "http://127.0.0.1:{port}"


def urlset(*paths: str) -> bytes:
    body = "".join(f"<url><loc>{B}{p}</loc></url>" for p in paths)
    return b"<urlset " + NS + b">" + body.encode() + b"</urlset>"


def index(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return b"<sitemapindex " + NS + b">" + body.encode() + b"</sitemapindex>"


def ok(body: bytes):
    return (200, body, {"Content-Type": "application/xml"})


def routes():
    return {
        "/robots.txt": ok(
            ("User-agent: *\nDisallow: /admin/\n"
             f"Sitemap: {B}/sitemap-index.xml\nSitemap: {B}/products.xml\nSitemap: {B}/sitemap-index.xml\n").encode()
        ),
        "/sitemap-index.xml": ok(index(
            f"{B}/products.xml", f"{B}/blog.xml", f"{B}/news.xml", f"{B}/malformed.xml",
            f"{B}/missing.xml", f"{B}/broken.xml", f"{B}/blog.xml", "https://evil.example.org/x.xml",
            "not a url", "",
        )),
        "/products.xml": ok(urlset("/products/1", "/products/2", "/products/2/", "/docs/manual.pdf")),
        "/blog.xml": ok(urlset("/blog/a", "/blog/b", "/products/1")),
        "/news.xml": ok(urlset("/news/1")),
        "/malformed.xml": ok(b"<urlset><url><loc>http://x"),
        "/broken.xml": (500, b"boom", {}),
    }


def discover(port: int, **limits):
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy, seed_url=f"http://127.0.0.1:{port}")
    fetcher = HttpFetcher(evaluator, resolver=lambda host: ["127.0.0.1"], timeout=5)
    return SitemapDiscovery(evaluator, fetcher, **limits).discover(f"http://127.0.0.1:{port}")


def test_full_tree(local_network):  # noqa: F811
    with server(routes()) as port:
        r = discover(port)
    base = f"http://127.0.0.1:{port}"
    assert r.urls == [f"{base}{p}" for p in (
        "/products/1", "/products/2", "/blog/a", "/blog/b", "/news/1")]
    by_name = {o.url.rsplit("/", 1)[1]: o for o in r.sitemaps}
    assert set(by_name) == {"sitemap-index.xml", "products.xml", "blog.xml", "news.xml",
                            "malformed.xml", "missing.xml", "broken.xml"}
    # duplicates fetched once
    assert len(r.sitemaps) == 7  # no fetch for the evil host, "not a url", or the empty loc
    # isolated failures
    assert by_name["malformed.xml"].parse_status is S.MALFORMED
    assert by_name["missing.xml"].fetch_status is FetchStatus.NOT_FOUND
    assert by_name["broken.xml"].fetch_status is FetchStatus.SERVER_ERROR
    assert {o.url.rsplit("/", 1)[1] for o in r.failed_sitemaps} == {"malformed.xml", "missing.xml", "broken.xml"}
    # policy rejections: evil domain + invalid child locs, pdf page, duplicate pages
    assert r.rejections[RejectReason.OUT_OF_SCOPE] == 1
    assert r.rejections[RejectReason.INVALID_URL] == 1  # "not a url" (empty loc is counted by the parser)
    assert r.rejections[RejectReason.NON_HTML_EXTENSION] == 1
    assert r.rejections[RejectReason.DUPLICATE] == 2


def test_basic_sitemap_only(local_network):  # noqa: F811
    with server({"/sitemap.xml": ok(urlset("/a", "/b"))}) as port:
        r = discover(port)
    assert r.used_fallback and r.urls == [f"http://127.0.0.1:{port}/a", f"http://127.0.0.1:{port}/b"]


def test_unknown_root_and_missing_loc(local_network):  # noqa: F811
    with server({
        "/robots.txt": ok(f"Sitemap: {B}/odd.xml\nSitemap: {B}/noloc.xml\n".encode()),
        "/odd.xml": ok(b"<html><body>hi</body></html>"),
        "/noloc.xml": ok(b"<urlset><url><lastmod>2026-01-01</lastmod></url></urlset>"),
    }) as port:
        r = discover(port)
    assert r.urls == []
    parse = {o.url.rsplit("/", 1)[1]: o.parse_status for o in r.sitemaps}
    assert parse == {"odd.xml": S.UNSUPPORTED, "noloc.xml": S.OK}


def test_resource_limits(local_network):  # noqa: F811
    with server({
        "/sitemap.xml": ok(index(*(f"{B}/s{i}.xml" for i in range(6)))),
        **{f"/s{i}.xml": ok(urlset(f"/p{i}a", f"/p{i}b")) for i in range(6)},
    }) as port:
        by_files = discover(port, max_sitemaps=3)
        by_urls = discover(port, max_urls=3)
    assert by_files.sitemap_limit_reached and len(by_files.sitemaps) == 3 and len(by_files.urls) == 4
    assert by_urls.url_limit_reached and len(by_urls.urls) == 3


def test_child_redirect_to_internal_address_is_blocked(local_network):  # noqa: F811
    with server({
        "/sitemap.xml": ok(index(f"{B}/redir.xml", f"{B}/leaf.xml")),
        "/redir.xml": (302, b"", {"Location": "http://169.254.169.254/latest/meta-data"}),
        "/leaf.xml": ok(urlset("/leaf")),
    }) as port:
        r = discover(port)
    assert r.urls == [f"http://127.0.0.1:{port}/leaf"]
    assert {o.url.rsplit("/", 1)[1]: o.fetch_status for o in r.failed_sitemaps} == {
        "redir.xml": FetchStatus.BLOCKED}


def test_oversize_child_over_http(local_network):  # noqa: F811
    big = urlset(*[f"/p{i}" for i in range(400)])
    with server({"/sitemap.xml": ok(index(f"{B}/big.xml", f"{B}/leaf.xml")),
                 "/big.xml": ok(big), "/leaf.xml": ok(urlset("/leaf"))}) as port:
        policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
        ev = UrlEvaluator(policy, seed_url=f"http://127.0.0.1:{port}")
        fetcher = HttpFetcher(ev, resolver=lambda h: ["127.0.0.1"], max_bytes=2000)
        r = SitemapDiscovery(ev, fetcher, max_sitemap_bytes=2000).discover(f"http://127.0.0.1:{port}")
    assert r.urls == [f"http://127.0.0.1:{port}/leaf"]
    assert {o.url.rsplit("/", 1)[1]: o.fetch_status for o in r.failed_sitemaps} == {"big.xml": FetchStatus.TOO_LARGE}


def test_nested_indexes_over_http(local_network):  # noqa: F811
    with server({
        "/robots.txt": ok(f"Sitemap: {B}/root-index.xml\n".encode()),
        "/root-index.xml": ok(index(f"{B}/products-index.xml", f"{B}/malformed.xml", f"{B}/another-valid.xml")),
        "/products-index.xml": ok(index(f"{B}/products-2026-index.xml")),
        "/products-2026-index.xml": ok(index(f"{B}/products-2026-01.xml", f"{B}/cycle-back.xml")),
        "/cycle-back.xml": ok(index(f"{B}/root-index.xml")),
        "/products-2026-01.xml": ok(urlset("/products/2026/01/a", "/products/2026/01/b")),
        "/malformed.xml": ok(b"<urlset><url>"),
        "/another-valid.xml": ok(urlset("/other")),
    }) as port:
        r = discover(port)
    base = f"http://127.0.0.1:{port}"
    assert r.urls == [f"{base}/products/2026/01/a", f"{base}/products/2026/01/b", f"{base}/other"]
    assert r.max_depth_seen == 3 and r.duplicate_sitemap_refs == 1
    assert [o.url.rsplit("/", 1)[1] for o in r.failed_sitemaps] == ["malformed.xml"]


def test_depth_and_count_limits_over_http(local_network):  # noqa: F811
    with server({
        "/sitemap.xml": ok(index(f"{B}/i1.xml")),
        "/i1.xml": ok(index(f"{B}/i2.xml")),
        "/i2.xml": ok(index(f"{B}/leaf.xml")),
        "/leaf.xml": ok(urlset("/leaf")),
    }) as port:
        shallow = discover(port, max_depth=1)
        deep = discover(port, max_depth=3)
    assert shallow.urls == [] and shallow.depth_limit_reached and shallow.skipped_by_depth == 1
    assert deep.urls == [f"http://127.0.0.1:{port}/leaf"] and not deep.depth_limit_reached
