import pytest

from app.urls import SeenUrls, UrlPolicy, dedupe_key, extract_canonical_href, resolve_canonical

PAGE = "https://www.example.com/team/alice?utm_source=x"


class TestExtractCanonical:
    def test_basic(self):
        html = '<html><head><title>x</title><link rel="canonical" href="https://example.com/team/alice"></head><body></body></html>'
        assert extract_canonical_href(html) == "https://example.com/team/alice"

    @pytest.mark.parametrize(
        "tag",
        ["<LINK REL=CANONICAL HREF='/a'>", '<link href="/a" rel="canonical">', '<link rel="canonical alternate" href="/a"/>', '<link rel = "canonical"\n href = "/a" >'],
    )
    def test_attribute_variants(self, tag):
        assert extract_canonical_href(f"<head>{tag}</head>") == "/a"

    def test_ignores_other_links(self):
        html = '<head><link rel="stylesheet" href="/s.css"><link rel="alternate" href="/x"></head>'
        assert extract_canonical_href(html) is None

    def test_first_canonical_wins(self):
        html = '<head><link rel="canonical" href="/one"><link rel="canonical" href="/two"></head>'
        assert extract_canonical_href(html) == "/one"

    def test_ignores_body_canonical(self):
        assert extract_canonical_href('<head></head><body><link rel="canonical" href="/x"></body>') is None

    def test_blank_href_ignored(self):
        assert extract_canonical_href('<head><link rel="canonical" href=" "></head>') is None

    def test_malformed_html_does_not_raise(self):
        assert extract_canonical_href("<<<>>><head><link rel=canonical href=/x") in (None, "/x")
        assert extract_canonical_href("") is None

    def test_huge_document_is_bounded(self):
        html = "<head>" + "<!-- x -->" * 100_000 + '<link rel="canonical" href="/late"></head>'
        assert extract_canonical_href(html) is None


class TestResolveCanonical:
    def test_same_page_no_change(self):
        r = resolve_canonical("https://example.com/a", "https://example.com/a/")
        assert r.url == "https://example.com/a" and not r.changed and r.reason == ""

    def test_relative_canonical_resolved_and_normalised(self):
        r = resolve_canonical("https://example.com/a/b?x=1&utm_source=n", "/a/b?utm_medium=z")
        assert r.url == "https://example.com/a/b" and r.changed

    def test_www_and_scheme_canonicalisation_accepted(self):
        r = resolve_canonical("http://example.com/team", "https://www.example.com/team")
        assert r.url == "https://www.example.com/team" and r.changed

    def test_subdomain_of_same_registered_domain_accepted(self):
        assert resolve_canonical("https://m.example.com/a", "https://www.example.com/a").url == "https://www.example.com/a"

    def test_cross_domain_ignored(self):
        r = resolve_canonical("https://example.com/a", "https://evil.com/a")
        assert r.url is None and r.reason == "cross_domain"

    def test_cross_domain_when_allowed(self):
        p = UrlPolicy(canonical_allow_cross_domain=True)
        assert resolve_canonical("https://example.com/a", "https://other.org/a", p).url == "https://other.org/a"

    def test_lookalike_domain_ignored(self):
        assert resolve_canonical("https://example.com/a", "https://example.com.evil.com/a").reason == "cross_domain"

    @pytest.mark.parametrize("href", ["http://localhost/a", "http://169.254.169.254/x", "http://127.0.0.1/", "http://10.0.0.1/"])
    def test_internal_canonical_ignored_even_when_cross_domain_allowed(self, href):
        r = resolve_canonical("https://example.com/a", href, UrlPolicy(canonical_allow_cross_domain=True))
        assert r.url is None and r.reason == "ssrf_blocked"

    def test_root_canonical_ignored(self):
        r = resolve_canonical("https://example.com/team/alice", "https://example.com/")
        assert r.url is None and r.reason == "root_canonical"
        assert resolve_canonical("https://example.com/", "https://example.com").url == "https://example.com/"
        assert resolve_canonical("https://example.com/a", "/", UrlPolicy(canonical_ignore_root=False)).url == "https://example.com/"

    def test_canonical_that_drops_pagination_ignored(self):
        r = resolve_canonical("https://example.com/list?page=3", "https://example.com/list")
        assert r.url is None and r.reason == "drops_pagination"
        r = resolve_canonical("https://example.com/list?page=3", "https://example.com/list?page=2")
        assert r.reason == "drops_pagination"
        assert resolve_canonical("https://example.com/list?page=3&x=1", "https://example.com/list?page=3").url

    def test_page_one_may_point_to_unpaginated_listing(self):
        assert resolve_canonical("https://example.com/list?page=1", "https://example.com/list").url == "https://example.com/list"

    @pytest.mark.parametrize("href", [None, "", "  ", "mailto:a@b.com", "javascript:void(0)", "http://", "https://user@x.com/"])
    def test_missing_or_invalid_ignored(self, href):
        assert resolve_canonical("https://example.com/a", href).url is None

    def test_reasons_are_reported(self):
        assert resolve_canonical("https://example.com/a", None).reason == "missing"
        assert resolve_canonical("https://example.com/a", "mailto:x@y.z").reason.startswith("invalid:")


class TestDedupeKey:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("https://example.com/team", "https://example.com/team/"),
            ("http://example.com/team", "https://example.com/team"),
            ("https://www.example.com/team", "https://example.com/team"),
            ("https://example.com/team/index.html", "https://example.com/team"),
            ("https://example.com/index.php", "https://example.com/"),
            ("https://example.com/team/Default.ASPX", "https://example.com/team"),
            ("https://EXAMPLE.com/team#x", "https://example.com/team"),
            ("https://example.com/t?b=2&a=1", "https://example.com/t?a=1&b=2"),
            ("https://example.com/t?a=1&utm_source=x", "https://example.com/t?a=1"),
            ("https://example.com/a/../team", "https://example.com/team"),
            ("https://example.com/%7Ebob", "https://example.com/~bob"),
        ],
    )
    def test_equivalent_urls_collide(self, a, b):
        assert dedupe_key(a) == dedupe_key(b)

    @pytest.mark.parametrize(
        "a,b",
        [
            ("https://example.com/Team", "https://example.com/team"),  # paths are case-sensitive
            ("https://example.com/t?a=1", "https://example.com/t?a=2"),
            ("https://example.com/t", "https://example.com/t?a=1"),
            ("https://a.example.com/t", "https://example.com/t"),
            ("https://example.com/team/index.html", "https://example.com/team/index.html?x=1"),
            ("https://example.com:8080/t", "https://example.com/t"),
            ("https://example.com/a", "https://example.org/a"),
        ],
    )
    def test_different_urls_do_not_collide(self, a, b):
        p = UrlPolicy(allowed_ports=None)
        assert dedupe_key(a, p) != dedupe_key(b, p)

    def test_strictness_is_configurable(self):
        strict = UrlPolicy(dedupe_ignore_scheme=False, dedupe_ignore_www=False, dedupe_strip_index_documents=False)
        assert dedupe_key("http://example.com/t", strict) != dedupe_key("https://example.com/t", strict)
        assert dedupe_key("https://www.example.com/t", strict) != dedupe_key("https://example.com/t", strict)
        assert dedupe_key("https://example.com/t/index.html", strict) != dedupe_key("https://example.com/t", strict)


class TestSeenUrls:
    def test_add_and_contains(self):
        seen = SeenUrls()
        assert seen.add("https://example.com/team") is True
        assert seen.add("https://www.example.com/team/") is False
        assert "http://example.com/team#bio" in seen
        assert "https://example.com/other" not in seen
        assert len(seen) == 1

    def test_canonical_registration(self):
        seen = SeenUrls()
        seen.add("https://example.com/a?ref=1")
        # canonical not yet known: mark it, page is not a duplicate
        assert seen.register_canonical("https://example.com/a?ref=1", "https://example.com/a") is False
        assert "https://example.com/a" in seen
        # a second alias of the same page now finds the canonical already known
        seen.add("https://example.com/a?ref=2")
        assert seen.register_canonical("https://example.com/a?ref=2", "https://example.com/a") is True

    def test_canonical_equal_to_page_is_not_duplicate(self):
        seen = SeenUrls()
        seen.add("https://example.com/a")
        assert seen.register_canonical("https://example.com/a", "https://example.com/a/") is False
