from datetime import date

import pytest

from app.urls import RejectReason, UrlDecision, UrlEvaluator, UrlGate, UrlPolicy

TODAY = date(2026, 9, 30)
SEED = "https://www.example.com/"


def evaluator(**kw):
    return UrlEvaluator(UrlPolicy(**kw), seed_url=SEED, clock=lambda: TODAY)


class TestEvaluator:
    def test_allowed_decision_carries_normalised_url(self):
        d = evaluator().evaluate("/Team/?utm_source=x#top", base=SEED)
        assert isinstance(d, UrlDecision) and d.allowed and bool(d)
        assert d.url == "https://www.example.com/Team"
        assert d.reason is None

    def test_rejected_decision_is_falsey_and_explains(self):
        d = evaluator().evaluate("javascript:void(0)", base=SEED)
        assert not d and d.reason == RejectReason.UNSUPPORTED_SCHEME and d.url is None

    def test_rejected_after_normalisation_keeps_url(self):
        d = evaluator().evaluate("/logo.png", base=SEED)
        assert d.reason == RejectReason.NON_HTML_EXTENSION and d.url == "https://www.example.com/logo.png"

    @pytest.mark.parametrize(
        "raw,reason",
        [
            ("", RejectReason.EMPTY),
            ("mailto:a@b.co", RejectReason.UNSUPPORTED_SCHEME),
            ("https://user:pw@www.example.com/", RejectReason.CREDENTIALS_IN_URL),
            ("http://localhost/", RejectReason.SSRF_BLOCKED),
            ("https://evil.com/", RejectReason.OUT_OF_SCOPE),
            ("https://mail.example.com/", RejectReason.SUBDOMAIN_NOT_RELEVANT),
            ("https://www.example.com:8443/", RejectReason.PORT_NOT_ALLOWED),
            ("/a.zip", RejectReason.NON_HTML_EXTENSION),
            ("/a/a/a/a", RejectReason.TRAP_REPEATED_SEGMENTS),
            ("/list?page=500", RejectReason.TRAP_PAGINATION),
            ("/events/2001/05", RejectReason.TRAP_CALENDAR),
            ("/x" * 20, RejectReason.TRAP_DEPTH),
        ],
    )
    def test_reasons(self, raw, reason):
        assert evaluator().evaluate(raw, base=SEED).reason == reason

    def test_evaluate_never_raises_on_hostile_input(self):
        ev = evaluator()
        hostile = ["\x00", "http://[", "http://a b/", "%", "http://@/", "http://:/", "\ud800", "a" * 100_000, "http://" + "a." * 500 + "com", "////", "http:///", "http://[::1"]
        for raw in hostile:
            d = ev.evaluate(raw, base=SEED)
            assert d.allowed is False or d.url is not None

    def test_evaluator_is_stateless(self):
        ev = evaluator()
        assert ev.evaluate("/team", base=SEED).allowed
        assert ev.evaluate("/team", base=SEED).allowed

    def test_rejections_are_logged_structurally(self, caplog):
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.urls.engine"):
            evaluator().evaluate("http://localhost/", base=SEED)
        rec = next(r for r in caplog.records if r.message == "url_rejected")
        assert rec.reason == "ssrf_blocked" and rec.url == "http://localhost/"

    def test_seed_is_normalised_with_default_scheme(self):
        ev = UrlEvaluator(seed_url="Example.com/team")
        assert ev.seed.url == "https://example.com/team"


class TestGate:
    def make(self, **kw):
        return UrlGate(evaluator(**kw))

    def test_admits_once(self):
        g = self.make()
        assert g.admit("/team", SEED).allowed
        for variant in ["/team/", "/team#x", "https://example.com/team", "http://www.example.com/team", "/team/index.html", "/team?utm_source=a"]:
            d = g.admit(variant, SEED)
            assert d.reason == RejectReason.DUPLICATE, variant
            assert d.url is not None
        assert g.admitted == 1
        assert g.rejections[RejectReason.DUPLICATE] == 6

    def test_rejections_counted_by_reason(self):
        g = self.make()
        g.admit("mailto:x@y.z", SEED)
        g.admit("javascript:1", SEED)
        g.admit("http://localhost", SEED)
        assert g.rejections[RejectReason.UNSUPPORTED_SCHEME] == 2
        assert g.rejections[RejectReason.SSRF_BLOCKED] == 1
        assert g.admitted == 0

    def test_rejected_urls_are_not_remembered(self):
        g = self.make(max_query_variants_per_path=1)
        assert g.admit("/s?a=1", SEED).allowed
        assert g.admit("/s?a=2", SEED).reason == RejectReason.TRAP_QUERY_VARIANTS
        # still a trap on re-sight (not mislabelled as a duplicate), and the slot did not leak
        assert g.admit("/s?a=2", SEED).reason == RejectReason.TRAP_QUERY_VARIANTS
        assert g.admit("/s?a=1", SEED).reason == RejectReason.DUPLICATE

    def test_duplicates_do_not_consume_trap_budget(self):
        g = self.make(max_query_variants_per_path=2)
        assert g.admit("/s?a=1", SEED).allowed
        for _ in range(10):
            assert g.admit("/s?a=1&utm_source=z", SEED).reason == RejectReason.DUPLICATE
        assert g.admit("/s?a=2", SEED).allowed

    def test_crawl_of_a_trappy_site_terminates_with_bounded_frontier(self):
        """Simulate a site with an infinite calendar, endless pagination and a search space."""
        g = UrlGate(UrlEvaluator(UrlPolicy(), SEED, clock=lambda: TODAY))
        links = []
        links += [f"/calendar/{y}/{m:02d}" for y in range(1990, 2100) for m in range(1, 13)]
        links += [f"/blog?page={p}" for p in range(1, 5000)]
        links += [f"/search?q=term{i}" for i in range(5000)]
        links += [f"/loop{'/x' * k}" for k in range(1, 60)]
        links += ["/team", "/contact", "/about"]
        admitted = [u for u in links if g.admit(u, SEED).allowed]
        assert len(admitted) < 200
        assert {"/team", "/contact", "/about"} <= set(admitted)
        assert g.rejections[RejectReason.TRAP_CALENDAR] > 1000
        assert g.rejections[RejectReason.TRAP_PAGINATION] > 4000
        assert g.rejections[RejectReason.TRAP_QUERY_VARIANTS] > 4900

    def test_empty_page_feedback(self):
        g = self.make()
        assert g.admit("/blog?page=2", SEED).allowed
        g.record_empty_page("https://www.example.com/blog?page=3")
        assert g.admit("/blog?page=3", SEED).reason == RejectReason.TRAP_PAGINATION
        assert g.admit("/blog?page=4", SEED).reason == RejectReason.TRAP_PAGINATION

    def test_canonical_flow(self):
        g = self.make()
        assert g.admit("/team/alice?ref=nav", SEED).allowed
        # page declares a clean canonical: accepted and remembered
        r = g.record_canonical("https://www.example.com/team/alice?ref=nav", "/team/alice")
        assert r.url == "https://www.example.com/team/alice" and r.reason == ""
        assert g.admit("/team/alice", SEED).reason == RejectReason.DUPLICATE
        # `ref` is deliberately not treated as tracking, so this is a distinct URL
        assert g.admit("/team/alice?ref=footer", SEED).allowed

    def test_canonical_duplicate_of_seen_is_reported(self):
        g = self.make()
        g.admit("/a", SEED)
        g.admit("/a?view=print", SEED)
        first = g.record_canonical("https://www.example.com/a", "https://www.example.com/a")
        assert not first.changed
        second = g.record_canonical("https://www.example.com/a?view=print", "https://www.example.com/a")
        assert second.reason == "duplicate_of_seen" and second.url == "https://www.example.com/a"

    def test_bad_canonical_hint_is_ignored(self):
        g = self.make()
        r = g.record_canonical("https://www.example.com/a", "http://169.254.169.254/")
        assert r.url is None

    def test_redirect_helper_matches_admission_rules(self):
        ev = evaluator()
        assert ev.evaluate_redirect("https://www.example.com/old", "/new").url == "https://www.example.com/new"
        assert ev.evaluate_redirect("https://www.example.com/old", "http://10.0.0.1/").reason == RejectReason.SSRF_BLOCKED
