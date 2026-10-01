"""Phase 5.3: recursive sitemap discovery (fake fetcher; deterministic, no sockets/DNS/time)."""

from __future__ import annotations

from app.discovery import DiscoveryResult, FetchStatus, SitemapDiscovery, SitemapKind
from app.discovery.sitemap import SitemapParseStatus as S
from app.urls import RejectReason, UrlEvaluator
from tests.test_discovery_index import index, robots
from tests.test_discovery_service import HOST, FakeFetcher, urlset


def run(routes, **limits) -> tuple[DiscoveryResult, FakeFetcher]:
    fetcher = FakeFetcher(routes)
    return SitemapDiscovery(UrlEvaluator(seed_url=HOST), fetcher, **limits).discover(HOST), fetcher


def u(name: str) -> str:
    return f"{HOST}/{name}"


def chain(levels: int, leaf_pages=("leaf",)) -> dict[str, object]:
    """robots -> idx0 -> idx1 -> ... -> idx{levels-1} -> leaf.xml (idx0 is depth 0)."""
    routes: dict[str, object] = {u("robots.txt"): robots(u("idx0.xml"))}
    for i in range(levels):
        child = u(f"idx{i + 1}.xml") if i + 1 < levels else u("leaf.xml")
        routes[u(f"idx{i}.xml")] = index(child)
    routes[u("leaf.xml")] = urlset(*[u(p) for p in leaf_pages])
    return routes


class TestRecursion:
    def test_one_level(self):
        r, _ = run({u("robots.txt"): robots(u("i.xml")), u("i.xml"): index(u("a.xml")), u("a.xml"): urlset(u("p"))})
        assert r.urls == [u("p")] and r.max_depth_seen == 1

    def test_two_levels(self):
        r, f = run({
            u("robots.txt"): robots(u("root.xml")),
            u("root.xml"): index(u("products-index.xml"), u("blog.xml")),
            u("products-index.xml"): index(u("products-1.xml"), u("products-2.xml")),
            u("products-1.xml"): urlset(u("p1")), u("products-2.xml"): urlset(u("p2")),
            u("blog.xml"): urlset(u("b1")),
        })
        assert r.urls == [u("p1"), u("p2"), u("b1")] and r.max_depth_seen == 2
        assert f.calls[1:] == [u("root.xml"), u("products-index.xml"), u("products-1.xml"),
                               u("products-2.xml"), u("blog.xml")]  # depth-first, document order

    def test_spec_example_chain(self):
        r, _ = run({
            u("robots.txt"): robots(u("root-index.xml")),
            u("root-index.xml"): index(u("products-index.xml")),
            u("products-index.xml"): index(u("products-2026-index.xml")),
            u("products-2026-index.xml"): index(u("products-2026-01.xml")),
            u("products-2026-01.xml"): urlset(u("p")),
        })
        assert r.urls == [u("p")]
        assert [o.depth for o in r.sitemaps] == [0, 1, 2, 3]
        assert [o.kind for o in r.sitemaps] == [SitemapKind.INDEX] * 3 + [SitemapKind.URLSET]
        assert r.sitemaps[3].parent == u("products-2026-index.xml")

    def test_deeply_nested_to_default_limit(self):
        r, _ = run(chain(5))  # idx0..idx4 (depth 0-4) then leaf at depth 5 == default max_depth
        assert r.urls == [u("leaf")] and not r.depth_limit_reached and r.max_depth_seen == 5

    def test_mixed_top_level_index_and_plain(self):
        r, _ = run({u("robots.txt"): robots(u("i.xml"), u("plain.xml")), u("i.xml"): index(u("j.xml")),
                    u("j.xml"): index(u("a.xml")), u("a.xml"): urlset(u("a"), u("shared")),
                    u("plain.xml"): urlset(u("shared"), u("b"))})
        assert r.urls == [u("a"), u("shared"), u("b")]


class TestMaxDepth:
    def test_exactly_at_max_depth_is_fetched(self):
        r, _ = run(chain(3), max_depth=3)  # leaf sits at depth 3
        assert r.urls == [u("leaf")] and not r.depth_limit_reached

    def test_depth_exceeded_is_not_fetched_and_recorded(self):
        r, f = run(chain(3), max_depth=2)  # idx2 (depth 2) would need leaf at depth 3
        assert r.urls == [] and u("leaf.xml") not in f.calls
        assert r.depth_limit_reached and r.skipped_by_depth == 1
        limited = [o for o in r.sitemaps if o.depth_limited]
        assert [o.url for o in limited] == [u("idx2.xml")]

    def test_max_depth_zero_means_no_child_fetching(self):
        r, f = run(chain(2), max_depth=0)
        assert r.urls == [] and f.calls == [u("robots.txt"), u("idx0.xml")] and r.depth_limit_reached

    def test_max_depth_zero_still_reads_plain_sitemaps(self):
        r, _ = run({u("robots.txt"): robots(u("a.xml")), u("a.xml"): urlset(u("p"))}, max_depth=0)
        assert r.urls == [u("p")] and not r.depth_limit_reached

    def test_depth_limit_does_not_stop_shallow_branches(self):
        routes = chain(4) | {u("robots.txt"): robots(u("idx0.xml"), u("shallow.xml")),
                             u("shallow.xml"): urlset(u("s"))}
        r, _ = run(routes, max_depth=1)
        assert r.urls == [u("s")] and r.depth_limit_reached

    def test_very_deep_chain_terminates_at_limit(self):
        r, f = run(chain(200), max_depth=20)
        assert r.depth_limit_reached and r.max_depth_seen == 20 and len(f.calls) == 1 + 21

    def test_invalid_depth_rejected(self):
        import pytest

        for bad in (-1, 21):
            with pytest.raises(ValueError):
                SitemapDiscovery(UrlEvaluator(seed_url=HOST), FakeFetcher({}), max_depth=bad)


class TestCycles:
    def test_a_b_a(self):
        r, f = run({u("robots.txt"): robots(u("a.xml")), u("a.xml"): index(u("b.xml")),
                    u("b.xml"): index(u("a.xml"))})
        assert f.calls == [u("robots.txt"), u("a.xml"), u("b.xml")]
        assert r.duplicate_sitemap_refs == 1 and r.urls == []

    def test_a_b_c_a(self):
        r, f = run({u("robots.txt"): robots(u("a.xml")), u("a.xml"): index(u("b.xml")),
                    u("b.xml"): index(u("c.xml")), u("c.xml"): index(u("a.xml"))})
        assert len(f.calls) == 4 and r.duplicate_sitemap_refs == 1

    def test_cycle_with_valid_branch(self):
        r, _ = run({u("robots.txt"): robots(u("cycle-a.xml")),
                    u("cycle-a.xml"): index(u("cycle-b.xml"), u("ok.xml")),
                    u("cycle-b.xml"): index(u("cycle-a.xml"), u("ok2.xml")),
                    u("ok.xml"): urlset(u("p1")), u("ok2.xml"): urlset(u("p2"))})
        assert r.urls == [u("p2"), u("p1")]

    def test_self_reference(self):
        r, f = run({u("robots.txt"): robots(u("a.xml")), u("a.xml"): index(u("a.xml"), u("b.xml")),
                    u("b.xml"): urlset(u("p"))})
        assert f.calls.count(u("a.xml")) == 1 and r.urls == [u("p")]

    def test_cycle_detected_through_canonical_variants(self):
        r, f = run({u("robots.txt"): robots(u("a.xml")),
                    u("a.xml"): index(u("b.xml")),
                    u("b.xml"): index("https://EXAMPLE.com/a.xml#frag", "http://www.example.com/a.xml/")})
        assert len(f.calls) == 3 and r.duplicate_sitemap_refs == 2

    def test_duplicate_references_across_branches(self):
        r, f = run({u("robots.txt"): robots(u("root.xml")),
                    u("root.xml"): index(u("i1.xml"), u("i2.xml"), u("shared.xml")),
                    u("i1.xml"): index(u("shared.xml")), u("i2.xml"): index(u("shared.xml")),
                    u("shared.xml"): urlset(u("p"))})
        assert f.calls.count(u("shared.xml")) == 1 and r.urls == [u("p")]
        assert r.duplicate_sitemap_refs == 2

    def test_declared_twice(self):
        _, f = run({u("robots.txt"): robots(u("a.xml"), u("a.xml")), u("a.xml"): urlset(u("p"))})
        assert f.calls.count(u("a.xml")) == 1


class TestLimits:
    def test_sitemap_count_limit_across_levels(self):
        routes = {u("robots.txt"): robots(u("root.xml")),
                  u("root.xml"): index(u("i1.xml"), u("i2.xml")),
                  u("i1.xml"): index(*(u(f"a{i}.xml") for i in range(5))),
                  u("i2.xml"): index(*(u(f"b{i}.xml") for i in range(5)))}
        routes |= {u(f"{c}{i}.xml"): urlset(u(f"{c}{i}")) for c in "ab" for i in range(5)}
        r, f = run(routes, max_sitemaps=5)
        assert len(r.sitemaps) == 5 and r.sitemap_limit_reached
        assert len(f.calls) == 1 + 5 and r.skipped_sitemaps == 3  # unvisited references: a3, a4 and i2

    def test_url_count_limit_across_nested_branches(self):
        locs = [u(f"p{i}") for i in range(30)]
        r, _ = run({u("robots.txt"): robots(u("root.xml")), u("root.xml"): index(u("i.xml"), u("c.xml")),
                    u("i.xml"): index(u("a.xml"), u("b.xml")), u("a.xml"): urlset(*locs[:10]),
                    u("b.xml"): urlset(*locs[10:20]), u("c.xml"): urlset(*locs[20:])}, max_urls=15)
        assert r.urls == locs[:15] and r.url_limit_reached

    def test_url_limit_stops_fetching_more_sitemaps(self):
        r, f = run({u("robots.txt"): robots(u("root.xml")), u("root.xml"): index(u("a.xml"), u("b.xml")),
                    u("a.xml"): urlset(u("p1"), u("p2"), u("p3")), u("b.xml"): urlset(u("q"))}, max_urls=2)
        assert r.urls == [u("p1"), u("p2")] and u("b.xml") not in f.calls

    def test_limits_do_not_trigger_for_normal_trees(self):
        r, _ = run(chain(3))
        assert not (r.sitemap_limit_reached or r.url_limit_reached or r.depth_limit_reached)

    def test_large_index_fan_out_is_bounded(self):
        names = [u(f"s{i}.xml") for i in range(500)]
        routes = {u("robots.txt"): robots(u("root.xml")), u("root.xml"): index(*names)}
        routes |= {n: urlset(n.replace(".xml", "")) for n in names}
        r, f = run(routes, max_sitemaps=100)
        assert len(r.sitemaps) == 100 and len(f.calls) == 101 and r.skipped_sitemaps == 401


class TestErrorIsolationNested:
    def test_malformed_nested_sitemap_isolated(self):
        r, _ = run({
            u("robots.txt"): robots(u("root-index.xml")),
            u("root-index.xml"): index(u("valid-index.xml"), u("malformed.xml"), u("another-valid.xml")),
            u("valid-index.xml"): index(u("valid-pages.xml")),
            u("valid-pages.xml"): urlset(u("a")), u("malformed.xml"): b"<urlset><url><loc>",
            u("another-valid.xml"): urlset(u("b")),
        })
        assert r.urls == [u("a"), u("b")]
        assert [(o.url, o.parse_status) for o in r.failed_sitemaps] == [(u("malformed.xml"), S.MALFORMED)]

    def test_404_and_500_nested_isolated(self):
        r, _ = run({
            u("robots.txt"): robots(u("root.xml")), u("root.xml"): index(u("i.xml"), u("ok.xml")),
            u("i.xml"): index(u("m404.xml"), u("m500.xml"), u("deep-ok.xml")),
            u("m500.xml"): 500, u("deep-ok.xml"): urlset(u("d")), u("ok.xml"): urlset(u("o")),
        })
        assert r.urls == [u("d"), u("o")]
        assert {o.url.rsplit("/", 1)[1]: o.fetch_status for o in r.failed_sitemaps} == {
            "m404.xml": FetchStatus.NOT_FOUND, "m500.xml": FetchStatus.SERVER_ERROR}

    def test_failed_index_skips_only_its_own_subtree(self):
        r, _ = run({u("robots.txt"): robots(u("root.xml")),
                    u("root.xml"): index(u("bad-index.xml"), u("good-index.xml")),
                    u("bad-index.xml"): 500, u("good-index.xml"): index(u("g.xml")), u("g.xml"): urlset(u("p"))})
        assert r.urls == [u("p")]

    def test_fetcher_exception_nested(self):
        r, _ = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index(u("i.xml"), u("ok.xml")),
                    u("i.xml"): index(u("boom.xml")), u("boom.xml"): RuntimeError("x"), u("ok.xml"): urlset(u("p"))})
        assert r.urls == [u("p")]

    def test_unknown_root_recorded_with_diagnostic(self):
        r, _ = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index(u("rss.xml"), u("ok.xml")),
                    u("rss.xml"): b"<rss><channel/></rss>", u("ok.xml"): urlset(u("p"))})
        assert r.urls == [u("p")]
        odd = r.failed_sitemaps[0]
        assert odd.parse_status is S.UNSUPPORTED and odd.detail == "root:rss" and odd.kind is None

    def test_missing_and_empty_loc_diagnostics_on_nested_index(self):
        r, _ = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index(u("i.xml")),
                    u("i.xml"): (b"<sitemapindex><sitemap/><sitemap><loc></loc></sitemap>"
                                 + f"<sitemap><loc>{u('ok.xml')}</loc></sitemap></sitemapindex>".encode()),
                    u("ok.xml"): urlset(u("p"))})
        inner = r.sitemaps[1]
        assert inner.missing_locs == 1 and inner.empty_locs == 1 and r.urls == [u("p")]

    def test_invalid_nested_locs_counted_not_fetched(self):
        r, f = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index(u("i.xml")),
                    u("i.xml"): index("http://[bad", "mailto:x@example.com", "relative.xml", u("ok.xml")),
                    u("ok.xml"): urlset(u("p"))})
        assert f.calls == [u("robots.txt"), u("r.xml"), u("i.xml"), u("ok.xml")]
        assert r.sitemaps[1].rejected_children == 3 and r.urls == [u("p")]
        assert r.rejections[RejectReason.UNSUPPORTED_SCHEME] == 1 and r.rejections[RejectReason.INVALID_URL] == 2

    def test_valid_branches_succeed_when_others_fail(self):
        r, _ = run({
            u("robots.txt"): robots(u("root.xml")),
            u("root.xml"): index(u("i1.xml"), u("i2.xml"), u("i3.xml"), u("i4.xml")),
            u("i1.xml"): index(u("a.xml")), u("a.xml"): urlset(u("a")),
            u("i2.xml"): b"garbage", u("i3.xml"): 403,
            u("i4.xml"): index(u("x.xml"), u("b.xml")), u("x.xml"): b"<feed/>", u("b.xml"): urlset(u("b")),
        })
        assert r.urls == [u("a"), u("b")] and len(r.failed_sitemaps) == 3


class TestPolicyNested:
    def test_rejected_child_and_nested_sitemaps_never_fetched(self):
        r, f = run({
            u("robots.txt"): robots(u("root.xml")),
            u("root.xml"): index("https://evil.org/i.xml", "http://127.0.0.1/i.xml", u("i.xml")),
            u("i.xml"): index("http://169.254.169.254/n.xml", "http://localhost/n.xml", "https://other.net/n.xml",
                              u("ok.xml")),
            u("ok.xml"): urlset(u("p")),
        })
        assert f.calls == [u("robots.txt"), u("root.xml"), u("i.xml"), u("ok.xml")]
        assert r.rejections[RejectReason.OUT_OF_SCOPE] == 2 and r.rejections[RejectReason.SSRF_BLOCKED] == 3
        assert r.urls == [u("p")]

    def test_rejected_page_urls_in_nested_sitemap(self):
        r, _ = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index(u("i.xml")), u("i.xml"): index(u("a.xml")),
                    u("a.xml"): urlset(u("x.pdf"), "https://other.net/p", "http://10.1.1.1/p", u("ok"))})
        assert r.urls == [u("ok")]
        assert r.rejections[RejectReason.NON_HTML_EXTENSION] == 1 and r.rejections[RejectReason.SSRF_BLOCKED] == 1

    def test_nested_sitemap_url_with_extension_is_allowed_but_port_policy_applies(self):
        r, f = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index("https://example.com:8443/i.xml")})
        assert f.calls == [u("robots.txt"), u("r.xml")]
        assert r.rejections[RejectReason.PORT_NOT_ALLOWED] == 1

    def test_dedupe_of_pages_across_nested_levels(self):
        r, _ = run({u("robots.txt"): robots(u("r.xml")), u("r.xml"): index(u("i.xml"), u("b.xml")),
                    u("i.xml"): index(u("a.xml")), u("a.xml"): urlset(u("p"), u("q")),
                    u("b.xml"): urlset(u("p/"), u("p?utm_source=x"), u("z"))})
        assert r.urls == [u("p"), u("q"), u("z")] and r.rejections[RejectReason.DUPLICATE] == 2
