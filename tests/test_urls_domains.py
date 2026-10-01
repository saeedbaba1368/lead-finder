import pytest

from app.urls import RejectReason, UrlEvaluator, UrlPolicy, check_scope, registered_domain
from app.urls.domains import is_ip_host, is_relevant_subdomain, matches_domain, subdomain_labels


class TestRegisteredDomain:
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("example.com", "example.com"),
            ("www.example.com", "example.com"),
            ("a.b.c.example.com", "example.com"),
            ("example.co.uk", "example.co.uk"),
            ("www.team.example.co.uk", "example.co.uk"),
            ("shop.example.com.au", "example.com.au"),
            ("foo.co.jp", "foo.co.jp"),
            ("mail.company.com.az", "company.com.az"),
            ("user.github.io", "user.github.io"),
            ("www.user.github.io", "user.github.io"),
            ("app.herokuapp.com", "app.herokuapp.com"),
            ("xn--exmple-cua.com", "xn--exmple-cua.com"),
            ("example.museum", "example.museum"),
            ("localhost", "localhost"),
            ("8.8.8.8", "8.8.8.8"),
            ("[2001:db8::1]", "[2001:db8::1]"),
        ],
    )
    def test_cases(self, host, expected):
        assert registered_domain(host) == expected

    @pytest.mark.parametrize("host", ["co.uk", "com.au", "github.io", "com.az"])
    def test_public_suffix_has_no_registered_domain(self, host):
        assert registered_domain(host) is None

    def test_extra_public_suffixes(self):
        assert registered_domain("a.b.internal-co.example") == "internal-co.example"
        extra = frozenset({"corp.example"})
        assert registered_domain("team.acme.corp.example", extra) == "acme.corp.example"
        assert registered_domain("corp.example", extra) is None

    def test_helpers(self):
        assert is_ip_host("1.2.3.4") and is_ip_host("[::1]") and not is_ip_host("example.com")
        assert subdomain_labels("a.b.example.com", "example.com") == ("a", "b")
        assert subdomain_labels("example.com", "example.com") == ()
        assert matches_domain("a.evil.com", "evil.com") and matches_domain("evil.com", "evil.com")
        assert not matches_domain("notevil.com", "evil.com")


def scope(host, seed="example.com", **kw):
    return check_scope(host, seed, UrlPolicy(**kw))


class TestScopeRelevantMode:
    @pytest.mark.parametrize(
        "host",
        ["example.com", "www.example.com", "www2.example.com", "team.example.com", "about.example.com",
         "careers.example.com", "blog.example.com", "en.example.com", "de.example.com", "en-us.example.com",
         "team.eu.example.com", "people.us.example.com"],
    )
    def test_allowed(self, host):
        assert scope(host).allowed, host

    @pytest.mark.parametrize(
        "host",
        ["mail.example.com", "webmail.example.com", "cdn.example.com", "static.example.com", "api.example.com",
         "dev.example.com", "staging.example.com", "vpn.example.com", "admin.example.com", "ns1.example.com",
         "team.dev.example.com", "random.example.com", "shop.example.com", "team.mail.example.com"],
    )
    def test_not_relevant(self, host):
        r = scope(host)
        assert not r.allowed and r.reason == RejectReason.SUBDOMAIN_NOT_RELEVANT, host

    def test_blocked_label_beats_relevant_label(self):
        assert not scope("team.admin.example.com").allowed

    def test_configurable_labels(self):
        assert scope("shop.example.com", extra_relevant_labels=frozenset({"shop"})).allowed
        assert not scope("team.example.com", extra_blocked_labels=frozenset({"team"})).allowed

    def test_other_domains_out_of_scope(self):
        r = scope("evil.com")
        assert not r.allowed and r.reason == RejectReason.OUT_OF_SCOPE

    def test_lookalike_domains_out_of_scope(self):
        for host in ["example.com.evil.com", "notexample.com", "example.org", "www.example.com.evil.io", "example-com.net"]:
            assert scope(host).reason == RejectReason.OUT_OF_SCOPE, host

    def test_public_suffix_host(self):
        assert scope("co.uk", seed="example.co.uk").reason == RejectReason.PUBLIC_SUFFIX

    def test_sibling_github_pages_are_separate_sites(self):
        assert scope("alice.github.io", seed="alice.github.io").allowed
        assert scope("bob.github.io", seed="alice.github.io").reason == RejectReason.OUT_OF_SCOPE

    def test_multi_label_suffix_seed(self):
        assert scope("www.example.co.uk", seed="example.co.uk").allowed
        assert scope("other.co.uk", seed="example.co.uk").reason == RejectReason.OUT_OF_SCOPE


class TestScopeOtherModes:
    def test_exact(self):
        p = dict(subdomain_mode="exact")
        assert scope("example.com", **p).allowed
        assert not scope("www.example.com", **p).allowed
        assert not scope("team.example.com", **p).allowed

    def test_www(self):
        p = dict(subdomain_mode="www")
        assert scope("www.example.com", **p).allowed and scope("example.com", **p).allowed
        assert not scope("team.example.com", **p).allowed
        assert scope("example.com", seed="www.example.com", **p).allowed

    def test_all(self):
        p = dict(subdomain_mode="all")
        assert scope("anything.example.com", **p).allowed
        assert scope("mail.example.com", **p).allowed
        assert not scope("evil.com", **p).allowed

    def test_seed_subdomain_is_always_in_scope(self):
        assert scope("mail.example.com", seed="mail.example.com").allowed
        assert scope("dev.example.com", seed="dev.example.com", subdomain_mode="exact").allowed

    def test_seed_on_subdomain_reaches_apex_and_relevant_siblings(self):
        assert scope("example.com", seed="team.example.com").allowed
        assert scope("blog.example.com", seed="team.example.com").allowed
        assert not scope("mail.example.com", seed="team.example.com").allowed

    def test_extra_scope_domains(self):
        p = UrlPolicy(scope_domains=frozenset({"example.org"}))
        assert check_scope("www.example.org", "example.com", p).allowed
        assert not check_scope("mail.example.org", "example.com", p).allowed
        assert not check_scope("example.net", "example.com", p).allowed

    def test_ip_hosts_match_exactly(self):
        p = UrlPolicy(ssrf_allow_private_networks=True)
        assert check_scope("10.0.0.1", "10.0.0.1", p).allowed
        assert not check_scope("10.0.0.2", "10.0.0.1", p).allowed
        assert not check_scope("example.com", "10.0.0.1", p).allowed


class TestBlockedDomains:
    def test_blocked_domain_and_subdomains(self):
        p = UrlPolicy(blocked_domains=frozenset({"facebook.com"}), scope_domains=frozenset({"facebook.com"}))
        for host in ["facebook.com", "www.facebook.com", "m.facebook.com"]:
            r = check_scope(host, "example.com", p)
            assert r.reason == RejectReason.BLOCKED_DOMAIN

    def test_blocked_beats_seed_host(self):
        p = UrlPolicy(blocked_domains=frozenset({"example.com"}))
        assert check_scope("example.com", "example.com", p).reason == RejectReason.BLOCKED_DOMAIN

    def test_blocked_does_not_match_lookalike(self):
        p = UrlPolicy(blocked_domains=frozenset({"evil.com"}))
        assert check_scope("notevil.com", "notevil.com", p).allowed


def test_relevant_subdomain_function():
    p = UrlPolicy()
    assert is_relevant_subdomain((), p)
    assert is_relevant_subdomain(("www",), p)
    assert not is_relevant_subdomain(("mail",), p)
    assert not is_relevant_subdomain(("zzz",), p)


def test_evaluator_scope_without_seed_only_applies_blocklist():
    ev = UrlEvaluator(UrlPolicy(blocked_domains=frozenset({"bad.com"})))
    assert ev.evaluate("https://anything.org/x").allowed
    assert ev.evaluate("https://www.bad.com/x").reason == RejectReason.BLOCKED_DOMAIN


def test_evaluator_uses_scope_end_to_end():
    ev = UrlEvaluator(seed_url="https://www.example.com/")
    assert ev.evaluate("/team", base="https://www.example.com/").allowed
    assert ev.evaluate("https://example.com/team").allowed
    assert ev.evaluate("https://mail.example.com/").reason == RejectReason.SUBDOMAIN_NOT_RELEVANT
    assert ev.evaluate("https://twitter.com/x").reason == RejectReason.OUT_OF_SCOPE
    assert ev.evaluate("https://co.uk/").reason == RejectReason.PUBLIC_SUFFIX
