"""Discovery pipeline with a fake in-memory fetcher (no sockets)."""

from __future__ import annotations

from app.discovery import DiscoveryResult, FetchResult, FetchStatus, SitemapDiscovery
from app.discovery.fetcher import status_for_http_code
from app.discovery.sitemap import SitemapParseStatus as S
from app.urls import RejectReason, UrlEvaluator

HOST = "https://example.com"


def urlset(*locs: str) -> bytes:
    body = "".join(f"<url><loc>{u}</loc></url>" for u in locs)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'.encode()


class FakeFetcher:
    """Maps url -> body bytes | FetchStatus | int (HTTP status) | Exception."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        route = self.routes.get(url, 404)
        if isinstance(route, Exception):
            raise route
        if isinstance(route, FetchStatus):
            return FetchResult(url, route)
        if isinstance(route, int):
            return FetchResult(url, status_for_http_code(route), route)
        assert isinstance(route, bytes)
        return FetchResult(url, FetchStatus.OK, 200, route)


def run(routes: dict[str, object], target: str = HOST) -> tuple[DiscoveryResult, FakeFetcher]:
    fetcher = FakeFetcher(routes)
    evaluator = UrlEvaluator(seed_url=target)
    return SitemapDiscovery(evaluator, fetcher).discover(target), fetcher


class TestRobotsToSitemap:
    def test_one_declaration(self):
        r, f = run({
            f"{HOST}/robots.txt": b"User-agent: *\nDisallow: /admin/\nSitemap: https://example.com/s.xml\n",
            f"{HOST}/s.xml": urlset("https://example.com/page-1", "https://example.com/page-2"),
        })
        assert r.urls == [f"{HOST}/page-1", f"{HOST}/page-2"]
        assert not r.used_fallback
        assert f.calls == [f"{HOST}/robots.txt", f"{HOST}/s.xml"]  # no /sitemap.xml fetch

    def test_multiple_declarations(self):
        r, _ = run({
            f"{HOST}/robots.txt": b"Sitemap: https://example.com/a.xml\nSitemap: https://example.com/b.xml\n",
            f"{HOST}/a.xml": urlset("https://example.com/a1"),
            f"{HOST}/b.xml": urlset("https://example.com/b1"),
        })
        assert r.urls == [f"{HOST}/a1", f"{HOST}/b1"] and len(r.sitemaps) == 2

    def test_duplicate_declarations_fetched_once(self):
        r, f = run({
            f"{HOST}/robots.txt": (
                b"Sitemap: https://example.com/a.xml\nSitemap: https://EXAMPLE.com/a.xml\n"
                b"Sitemap: https://example.com/a.xml#frag\n"
            ),
            f"{HOST}/a.xml": urlset("https://example.com/p"),
        })
        assert f.calls.count(f"{HOST}/a.xml") == 1 and r.urls == [f"{HOST}/p"]

    def test_invalid_declaration_ignored_valid_one_used(self):
        r, f = run({
            f"{HOST}/robots.txt": b"Sitemap: nonsense\nSitemap: https://example.com/ok.xml\n",
            f"{HOST}/ok.xml": urlset("https://example.com/p"),
        })
        assert r.urls == [f"{HOST}/p"] and not r.used_fallback

    def test_out_of_scope_declaration_rejected_then_fallback(self):
        r, f = run({
            f"{HOST}/robots.txt": b"Sitemap: https://evil.org/s.xml\n",
            f"{HOST}/sitemap.xml": urlset("https://example.com/p"),
        })
        assert r.used_fallback and r.urls == [f"{HOST}/p"]
        assert "https://evil.org/s.xml" not in f.calls
        assert r.rejections[RejectReason.OUT_OF_SCOPE] == 1

    def test_internal_declaration_never_fetched(self):
        r, f = run({
            f"{HOST}/robots.txt": b"Sitemap: http://169.254.169.254/latest/meta-data.xml\n",
        })
        assert all("169.254" not in c for c in f.calls)


class TestFallback:
    def test_robots_without_sitemap_falls_back(self):
        r, f = run({
            f"{HOST}/robots.txt": b"User-agent: *\nDisallow: /x\n",
            f"{HOST}/sitemap.xml": urlset("https://example.com/p"),
        })
        assert r.used_fallback and r.urls == [f"{HOST}/p"]
        assert f.calls == [f"{HOST}/robots.txt", f"{HOST}/sitemap.xml"]

    def test_empty_robots_falls_back(self):
        r, _ = run({f"{HOST}/robots.txt": b"", f"{HOST}/sitemap.xml": urlset("https://example.com/p")})
        assert r.used_fallback and r.urls == [f"{HOST}/p"]

    def test_malformed_robots_falls_back(self):
        r, _ = run({
            f"{HOST}/robots.txt": b"<html>oops</html>\x00\xff",
            f"{HOST}/sitemap.xml": urlset("https://example.com/p"),
        })
        assert r.urls == [f"{HOST}/p"]

    def test_robots_unavailable_falls_back(self):
        for status in (404, 403, 500, FetchStatus.TIMEOUT, FetchStatus.CONNECTION_ERROR):
            r, f = run({f"{HOST}/sitemap.xml": urlset("https://example.com/p")} | {f"{HOST}/robots.txt": status})
            assert r.urls == [f"{HOST}/p"], status
            assert r.used_fallback and f.calls[-1] == f"{HOST}/sitemap.xml"

    def test_declared_sitemap_does_not_fetch_fallback(self):
        _, f = run({f"{HOST}/robots.txt": b"Sitemap: https://example.com/a.xml\n", f"{HOST}/a.xml": 404})
        assert f"{HOST}/sitemap.xml" not in f.calls


class TestErrors:
    def test_robots_status_reported(self):
        for route, status in ((404, FetchStatus.NOT_FOUND), (403, FetchStatus.FORBIDDEN),
                              (500, FetchStatus.SERVER_ERROR), (FetchStatus.TIMEOUT, FetchStatus.TIMEOUT),
                              (FetchStatus.CONNECTION_ERROR, FetchStatus.CONNECTION_ERROR)):
            r, _ = run({f"{HOST}/robots.txt": route})
            assert r.robots_status is status and r.urls == [] and r.error is None

    def test_everything_missing(self):
        r, _ = run({})
        assert r.urls == [] and r.sitemaps[0].fetch_status is FetchStatus.NOT_FOUND

    def test_sitemap_http_errors(self):
        for code in (403, 500):
            r, _ = run({f"{HOST}/sitemap.xml": code})
            assert r.urls == [] and r.sitemaps[0].parse_status is None

    def test_fetcher_raising_does_not_crash(self):
        r, _ = run({f"{HOST}/robots.txt": RuntimeError("boom"), f"{HOST}/sitemap.xml": ValueError("x")})
        assert r.urls == [] and r.robots_status is FetchStatus.CONNECTION_ERROR

    def test_one_bad_sitemap_does_not_stop_others(self):
        r, _ = run({
            f"{HOST}/robots.txt": b"Sitemap: https://example.com/bad.xml\nSitemap: https://example.com/good.xml\n",
            f"{HOST}/bad.xml": b"<urlset><url><loc>",
            f"{HOST}/good.xml": urlset("https://example.com/p"),
        })
        assert r.urls == [f"{HOST}/p"]
        assert r.sitemaps[0].parse_status is S.MALFORMED

    def test_unusable_target(self):
        for target in ("", "ftp://example.com", "http://localhost", "javascript:alert(1)"):
            fetcher = FakeFetcher({})
            try:
                evaluator = UrlEvaluator(seed_url=HOST)
                r = SitemapDiscovery(evaluator, fetcher).discover(target)
            except Exception as exc:  # pragma: no cover
                raise AssertionError(f"raised for {target!r}: {exc!r}") from exc
            assert r.error and fetcher.calls == []



class TestUrlPolicyIntegration:
    def test_malformed_empty_and_unsupported_locs_rejected(self):
        r, _ = run({f"{HOST}/sitemap.xml": urlset(
            "", "http://[bad", "mailto:a@example.com", "javascript:alert(1)", "ftp://example.com/x",
            "https://example.com/ok",
        )})
        assert r.urls == [f"{HOST}/ok"]
        assert r.rejections[RejectReason.INVALID_URL] >= 1
        assert r.rejections[RejectReason.UNSUPPORTED_SCHEME] >= 1

    def test_policy_rejections(self):
        r, _ = run({f"{HOST}/sitemap.xml": urlset(
            "https://example.com/file.pdf",          # non-html extension
            "https://other-site.org/page",            # out of scope
            "http://127.0.0.1/admin",                 # SSRF
            "https://example.com/ok",
        )})
        assert r.urls == [f"{HOST}/ok"]
        assert r.rejections[RejectReason.NON_HTML_EXTENSION] == 1
        assert r.rejections[RejectReason.OUT_OF_SCOPE] == 1
        assert r.rejections[RejectReason.SSRF_BLOCKED] == 1

    def test_dedupe_uses_existing_canonicalisation(self):
        r, _ = run({f"{HOST}/sitemap.xml": urlset(
            "https://example.com/a", "https://EXAMPLE.com/a/", "https://example.com/a#x",
            "https://example.com/a?utm_source=z", "http://www.example.com/a", "https://example.com/b",
        )})
        assert r.urls == [f"{HOST}/a", f"{HOST}/b"]
        assert r.rejections[RejectReason.DUPLICATE] == 4

    def test_relative_locs_resolve_against_sitemap_url(self):
        r, _ = run({f"{HOST}/sitemap.xml": urlset("/rel", "rel2")})
        assert r.urls == [f"{HOST}/rel", f"{HOST}/rel2"]

    def test_sitemap_across_files_dedupes_globally(self):
        r, _ = run({
            f"{HOST}/robots.txt": b"Sitemap: https://example.com/a.xml\nSitemap: https://example.com/b.xml\n",
            f"{HOST}/a.xml": urlset("https://example.com/p"),
            f"{HOST}/b.xml": urlset("https://example.com/p", "https://example.com/q"),
        })
        assert r.urls == [f"{HOST}/p", f"{HOST}/q"]

    def test_bare_domain_target(self):
        r, f = run({"https://example.com/sitemap.xml": urlset("https://example.com/p")}, target="example.com")
        assert r.urls == [f"{HOST}/p"] and f.calls[0] == f"{HOST}/robots.txt"


class TestEvaluatorOptOut:
    def test_default_still_rejects_xml_and_txt(self):
        ev = UrlEvaluator(seed_url=HOST)
        assert ev.evaluate(f"{HOST}/sitemap.xml").reason is RejectReason.NON_HTML_EXTENSION
        assert ev.evaluate(f"{HOST}/sitemap.xml", check_extension=False).allowed
        assert not ev.evaluate("http://127.0.0.1/robots.txt", check_extension=False).allowed
