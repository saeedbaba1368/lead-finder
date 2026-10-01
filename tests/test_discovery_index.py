"""Phase 5.2: sitemap indexes and multiple sitemap files, via an in-memory fake fetcher."""

from __future__ import annotations

from app.discovery import DiscoveryResult, FetchStatus, SitemapDiscovery, SitemapKind
from app.discovery.sitemap import SitemapParseStatus as S
from app.urls import RejectReason, UrlEvaluator
from tests.test_discovery_service import HOST, FakeFetcher, urlset


def index(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in locs)
    return f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemapindex>'.encode()


def robots(*sitemaps: str) -> bytes:
    return ("User-agent: *\nDisallow: /admin/\n" + "".join(f"Sitemap: {s}\n" for s in sitemaps)).encode()


def run(routes, **limits) -> tuple[DiscoveryResult, FakeFetcher]:
    fetcher = FakeFetcher(routes)
    disc = SitemapDiscovery(UrlEvaluator(seed_url=HOST), fetcher, **limits)
    return disc.discover(HOST), fetcher


def tree(**extra):
    routes = {
        f"{HOST}/robots.txt": robots(f"{HOST}/sitemap-index.xml"),
        f"{HOST}/sitemap-index.xml": index(f"{HOST}/products.xml", f"{HOST}/blog.xml", f"{HOST}/news.xml"),
        f"{HOST}/products.xml": urlset(f"{HOST}/p1", f"{HOST}/p2"),
        f"{HOST}/blog.xml": urlset(f"{HOST}/b1"),
        f"{HOST}/news.xml": urlset(f"{HOST}/n1"),
    }
    routes.update(extra)
    return routes


class TestIndexFollowing:
    def test_robots_to_index_to_children(self):
        r, f = run(tree())
        assert r.urls == [f"{HOST}/p1", f"{HOST}/p2", f"{HOST}/b1", f"{HOST}/n1"]
        assert not r.used_fallback and r.error is None
        assert f.calls == [f"{HOST}/robots.txt", f"{HOST}/sitemap-index.xml", f"{HOST}/products.xml",
                           f"{HOST}/blog.xml", f"{HOST}/news.xml"]

    def test_outcomes_record_kind_and_parent(self):
        r, _ = run(tree())
        top, *children = r.sitemaps
        assert top.kind is SitemapKind.INDEX and top.parent is None and top.locs_found == 3
        assert [c.parent for c in children] == [f"{HOST}/sitemap-index.xml"] * 3
        assert [c.pages_admitted for c in children] == [2, 1, 1]

    def test_index_via_fallback_and_arbitrary_filename(self):
        r, _ = run({
            f"{HOST}/sitemap.xml": index(f"{HOST}/x/products-2026.xml"),
            f"{HOST}/x/products-2026.xml": urlset(f"{HOST}/p1"),
        })
        assert r.used_fallback and r.urls == [f"{HOST}/p1"]

    def test_type_decided_by_root_not_name(self):
        # a file called sitemap-index.xml that is a urlset, and a products.xml that is an index
        r, _ = run({
            f"{HOST}/robots.txt": robots(f"{HOST}/sitemap-index.xml", f"{HOST}/products.xml"),
            f"{HOST}/sitemap-index.xml": urlset(f"{HOST}/a"),
            f"{HOST}/products.xml": index(f"{HOST}/child.xml"),
            f"{HOST}/child.xml": urlset(f"{HOST}/b"),
        })
        assert r.urls == [f"{HOST}/a", f"{HOST}/b"]

    def test_relative_child_sitemap_locs_rejected_not_fetched(self):
        # the sitemap protocol requires absolute URLs, so child references are not guessed
        r, f = run({f"{HOST}/sitemap.xml": index("/maps/a.xml", "b.xml", "//example.com/c.xml", "not a url"),
                    f"{HOST}/maps/a.xml": urlset(f"{HOST}/a")})
        assert f.calls == [f"{HOST}/robots.txt", f"{HOST}/sitemap.xml"] and r.urls == []
        assert r.rejections[RejectReason.INVALID_URL] == 4


class TestMultipleSources:
    def test_multiple_declarations_all_processed(self):
        r, _ = run({
            f"{HOST}/robots.txt": robots(f"{HOST}/sitemap.xml", f"{HOST}/products.xml", f"{HOST}/blog.xml"),
            f"{HOST}/sitemap.xml": urlset(f"{HOST}/home"),
            f"{HOST}/products.xml": urlset(f"{HOST}/p1"),
            f"{HOST}/blog.xml": urlset(f"{HOST}/b1"),
        })
        assert r.urls == [f"{HOST}/home", f"{HOST}/p1", f"{HOST}/b1"]

    def test_mixed_index_and_plain_sitemaps(self):
        r, _ = run({
            f"{HOST}/robots.txt": robots(f"{HOST}/sitemap-index.xml", f"{HOST}/products.xml", f"{HOST}/blog.xml"),
            f"{HOST}/sitemap-index.xml": index(f"{HOST}/news.xml"),
            f"{HOST}/news.xml": urlset(f"{HOST}/n1", f"{HOST}/shared"),
            f"{HOST}/products.xml": urlset(f"{HOST}/p1", f"{HOST}/shared"),
            f"{HOST}/blog.xml": urlset(f"{HOST}/b1"),
        })
        assert r.urls == [f"{HOST}/n1", f"{HOST}/shared", f"{HOST}/p1", f"{HOST}/b1"]
        assert r.rejections[RejectReason.DUPLICATE] == 1

    def test_declared_sitemap_also_listed_in_index_fetched_once(self):
        _, f = run({
            f"{HOST}/robots.txt": robots(f"{HOST}/sitemap-index.xml", f"{HOST}/products.xml"),
            f"{HOST}/sitemap-index.xml": index(f"{HOST}/products.xml"),
            f"{HOST}/products.xml": urlset(f"{HOST}/p1"),
        })
        assert f.calls.count(f"{HOST}/products.xml") == 1

    def test_duplicate_child_references_fetched_once(self):
        r, f = run({
            f"{HOST}/sitemap.xml": index(f"{HOST}/a.xml", f"{HOST}/a.xml", f"{HOST}/A.xml".replace("A", "a") + "#x",
                                         "https://EXAMPLE.com/a.xml"),
            f"{HOST}/a.xml": urlset(f"{HOST}/p"),
        })
        assert f.calls.count(f"{HOST}/a.xml") == 1 and r.urls == [f"{HOST}/p"]

    def test_index_referencing_itself_is_not_refetched(self):
        r, f = run({f"{HOST}/sitemap.xml": index(f"{HOST}/sitemap.xml", f"{HOST}/a.xml"),
                    f"{HOST}/a.xml": urlset(f"{HOST}/p")})
        assert f.calls.count(f"{HOST}/sitemap.xml") == 1 and r.urls == [f"{HOST}/p"]


class TestErrorIsolation:
    def test_malformed_child_does_not_stop_siblings(self):
        r, _ = run({
            f"{HOST}/sitemap.xml": index(f"{HOST}/valid-1.xml", f"{HOST}/malformed.xml", f"{HOST}/valid-2.xml"),
            f"{HOST}/valid-1.xml": urlset(f"{HOST}/a"),
            f"{HOST}/malformed.xml": b"<urlset><url><loc>",
            f"{HOST}/valid-2.xml": urlset(f"{HOST}/b"),
        })
        assert r.urls == [f"{HOST}/a", f"{HOST}/b"]
        assert [o.url for o in r.failed_sitemaps] == [f"{HOST}/malformed.xml"]
        assert r.failed_sitemaps[0].parse_status is S.MALFORMED

    def test_404_500_403_timeout_children_isolated(self):
        r, _ = run({
            f"{HOST}/sitemap.xml": index(f"{HOST}/m404.xml", f"{HOST}/m500.xml", f"{HOST}/m403.xml",
                                         f"{HOST}/mt.xml", f"{HOST}/ok.xml"),
            f"{HOST}/m500.xml": 500, f"{HOST}/m403.xml": 403, f"{HOST}/mt.xml": FetchStatus.TIMEOUT,
            f"{HOST}/ok.xml": urlset(f"{HOST}/p"),
        })
        assert r.urls == [f"{HOST}/p"]
        statuses = {o.url.rsplit("/", 1)[1]: o.fetch_status for o in r.failed_sitemaps}
        assert statuses == {"m404.xml": FetchStatus.NOT_FOUND, "m500.xml": FetchStatus.SERVER_ERROR,
                            "m403.xml": FetchStatus.FORBIDDEN, "mt.xml": FetchStatus.TIMEOUT}

    def test_fetcher_raising_for_one_child_is_isolated(self):
        r, _ = run({f"{HOST}/sitemap.xml": index(f"{HOST}/boom.xml", f"{HOST}/ok.xml"),
                    f"{HOST}/boom.xml": RuntimeError("x"), f"{HOST}/ok.xml": urlset(f"{HOST}/p")})
        assert r.urls == [f"{HOST}/p"]

    def test_bad_child_documents(self):
        r, _ = run({
            f"{HOST}/sitemap.xml": index(*(f"{HOST}/{n}.xml" for n in ("empty", "root", "bin", "ok"))),
            f"{HOST}/empty.xml": b"", f"{HOST}/root.xml": b"<feed/>", f"{HOST}/bin.xml": bytes(range(256)),
            f"{HOST}/ok.xml": urlset(f"{HOST}/p"),
        })
        assert r.urls == [f"{HOST}/p"]
        assert {o.url.rsplit("/", 1)[1]: o.parse_status for o in r.failed_sitemaps if o.parse_status} == {
            "empty.xml": S.EMPTY, "root.xml": S.UNSUPPORTED, "bin.xml": S.MALFORMED}

    def test_malformed_top_level_does_not_stop_other_declarations(self):
        r, _ = run({
            f"{HOST}/robots.txt": robots(f"{HOST}/bad.xml", f"{HOST}/good.xml"),
            f"{HOST}/bad.xml": b"<<<", f"{HOST}/good.xml": urlset(f"{HOST}/p"),
        })
        assert r.urls == [f"{HOST}/p"]


class TestIndexValidation:
    def test_missing_empty_invalid_and_unsupported_child_locs(self):
        r, f = run({
            f"{HOST}/sitemap.xml": (
                b"<sitemapindex><sitemap><lastmod>x</lastmod></sitemap><sitemap><loc></loc></sitemap>"
                b"<sitemap><loc>http://[bad</loc></sitemap><sitemap><loc>mailto:a@b.co</loc></sitemap>"
                b"<sitemap><loc>ftp://example.com/x.xml</loc></sitemap>"
                + f"<sitemap><loc>{HOST}/ok.xml</loc></sitemap></sitemapindex>".encode()
            ),
            f"{HOST}/ok.xml": urlset(f"{HOST}/p"),
        })
        assert r.urls == [f"{HOST}/p"]
        assert f.calls == [f"{HOST}/robots.txt", f"{HOST}/sitemap.xml", f"{HOST}/ok.xml"]
        assert r.rejections[RejectReason.UNSUPPORTED_SCHEME] >= 2
        assert r.rejections[RejectReason.INVALID_URL] >= 1

    def test_index_with_no_valid_children(self):
        r, _ = run({f"{HOST}/sitemap.xml": b"<sitemapindex/>"})
        assert r.urls == [] and r.sitemaps[0].kind is SitemapKind.INDEX

    def test_unknown_root_child(self):
        r, _ = run({f"{HOST}/sitemap.xml": index(f"{HOST}/x.xml"), f"{HOST}/x.xml": b"<html/>"})
        assert r.urls == [] and r.sitemaps[1].parse_status is S.UNSUPPORTED


class TestUrlPolicyEnforcement:
    def test_child_sitemap_urls_are_policy_checked_before_fetching(self):
        r, f = run({f"{HOST}/sitemap.xml": index(
            "https://evil.org/s.xml", "http://127.0.0.1/s.xml", "http://169.254.169.254/s.xml",
            "http://localhost/s.xml", f"{HOST}/ok.xml")
            , f"{HOST}/ok.xml": urlset(f"{HOST}/p")})
        assert f.calls == [f"{HOST}/robots.txt", f"{HOST}/sitemap.xml", f"{HOST}/ok.xml"]
        assert r.rejections[RejectReason.OUT_OF_SCOPE] >= 1 and r.rejections[RejectReason.SSRF_BLOCKED] >= 2

    def test_page_urls_inside_children_are_policy_checked(self):
        r, _ = run({f"{HOST}/sitemap.xml": index(f"{HOST}/a.xml"),
                    f"{HOST}/a.xml": urlset(f"{HOST}/doc.pdf", "https://other.org/x", "http://10.0.0.1/x",
                                            "javascript:alert(1)", f"{HOST}/ok")})
        assert r.urls == [f"{HOST}/ok"]
        assert r.rejections[RejectReason.NON_HTML_EXTENSION] == 1
        assert r.rejections[RejectReason.OUT_OF_SCOPE] == 1

    def test_duplicate_pages_across_children(self):
        r, _ = run({f"{HOST}/sitemap.xml": index(f"{HOST}/a.xml", f"{HOST}/b.xml"),
                    f"{HOST}/a.xml": urlset(f"{HOST}/p", f"{HOST}/P2"),
                    f"{HOST}/b.xml": urlset(f"{HOST}/p/", f"{HOST}/p?utm_source=x", f"{HOST}/q")})
        assert r.urls == [f"{HOST}/p", f"{HOST}/P2", f"{HOST}/q"]


class TestLimits:
    def test_max_sitemaps(self):
        names = [f"{HOST}/s{i}.xml" for i in range(10)]
        routes = {f"{HOST}/sitemap.xml": index(*names)} | {n: urlset(f"{HOST}/p{i}") for i, n in enumerate(names)}
        r, f = run(routes, max_sitemaps=4)
        assert len(r.sitemaps) == 4 and r.sitemap_limit_reached and r.skipped_sitemaps == 7
        assert len(f.calls) == 1 + 4 and len(r.urls) == 3

    def test_max_urls(self):
        locs = [f"{HOST}/p{i}" for i in range(20)]
        r, _ = run({f"{HOST}/sitemap.xml": index(f"{HOST}/a.xml", f"{HOST}/b.xml"),
                    f"{HOST}/a.xml": urlset(*locs[:10]), f"{HOST}/b.xml": urlset(*locs[10:])}, max_urls=12)
        assert len(r.urls) == 12 and r.url_limit_reached and r.urls == locs[:12]

    def test_limits_not_flagged_when_not_hit(self):
        r, _ = run(tree())
        assert not r.sitemap_limit_reached and not r.url_limit_reached and r.skipped_sitemaps == 0

    def test_invalid_limits_rejected(self):
        import pytest

        for kw in ({"max_sitemaps": 0}, {"max_urls": 0}):
            with pytest.raises(ValueError):
                SitemapDiscovery(UrlEvaluator(seed_url=HOST), FakeFetcher({}), **kw)

    def test_oversize_child_xml(self):
        r, _ = run({f"{HOST}/sitemap.xml": index(f"{HOST}/big.xml", f"{HOST}/ok.xml"),
                    f"{HOST}/big.xml": urlset(*[f"{HOST}/p{i}" for i in range(200)]),
                    f"{HOST}/ok.xml": urlset(f"{HOST}/p")}, max_sitemap_bytes=1000)
        assert r.urls == [f"{HOST}/p"]
        assert r.failed_sitemaps[0].parse_status is S.TOO_LARGE
