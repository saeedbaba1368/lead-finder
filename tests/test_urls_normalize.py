import pytest

from app.urls import RejectReason, UrlPolicy, UrlRejected, normalize_url


def norm(raw, base=None, policy=None, **kw):
    return normalize_url(raw, base, policy, **kw) if policy else normalize_url(raw, base, **kw)


def rejected(raw, reason, base=None, policy=None, **kw):
    with pytest.raises(UrlRejected) as info:
        norm(raw, base, policy, **kw)
    assert info.value.reason == reason


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://example.com", "http://example.com/"),
        ("HTTP://EXAMPLE.COM/Path", "http://example.com/Path"),  # path case preserved
        ("https://example.com:443/", "https://example.com/"),
        ("http://example.com:80/x", "http://example.com/x"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a#!/spa/route", "https://example.com/a"),
        ("https://example.com/a?#frag", "https://example.com/a"),
        ("https://example.com/a?", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://exam\nple.com/\ta", "https://example.com/a"),
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/../../a", "https://example.com/a"),
        ("https://example.com/a//b///c", "https://example.com/a/b/c"),
        ("https://example.com/%7Euser", "https://example.com/~user"),
        ("https://example.com/%e2%82%ac", "https://example.com/%E2%82%AC"),
        ("https://example.com/a%2fb", "https://example.com/a%2Fb"),  # encoded slash preserved
        ("https://example.com/%2e%2e/secret", "https://example.com/secret"),
        ("https://example.com/%2E/a", "https://example.com/a"),
        ("https://example.com/€", "https://example.com/%E2%82%AC"),
        ("https://example.com/a b", "https://example.com/a%20b"),
        ("https://example.com/100%", "https://example.com/100%25"),
        ("https://example.com/%zz", "https://example.com/%25zz"),
        ("https://example.com/a;jsessionid=ABC123", "https://example.com/a"),
        ("https://example.com/a;JSESSIONID=ABC123?x=1", "https://example.com/a?x=1"),
        ("https://example.com/a\\b", "https://example.com/a/b"),
    ],
)
def test_normalization_table(raw, expected):
    assert norm(raw).url == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://example.com/team/", "https://example.com/team"),
        ("https://example.com/team", "https://example.com/team"),
        ("https://example.com/team//", "https://example.com/team"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/a/b/./", "https://example.com/a/b"),
        ("https://example.com/a/b/..", "https://example.com/a"),
    ],
)
def test_trailing_slash_strip(raw, expected):
    assert norm(raw).url == expected


def test_trailing_slash_keep_policy():
    p = UrlPolicy(trailing_slash="keep")
    assert norm("https://example.com/team/", policy=p).url == "https://example.com/team/"
    assert norm("https://example.com/team", policy=p).url == "https://example.com/team"
    assert norm("https://example.com/", policy=p).url == "https://example.com/"


def test_collapse_slashes_can_be_disabled():
    p = UrlPolicy(collapse_slashes=False)
    assert norm("https://example.com/a//b", policy=p).url == "https://example.com/a//b"


def test_fragment_removed_and_query_kept():
    assert norm("https://example.com/p?a=1#x?y=2").url == "https://example.com/p?a=1"


class TestQuery:
    def test_sorted_and_stable_for_repeated_keys(self):
        assert norm("https://e.com/?b=2&a=2&a=1&c").url == "https://e.com/?a=2&a=1&b=2&c"

    def test_sorting_can_be_disabled(self):
        p = UrlPolicy(sort_query=False)
        assert norm("https://e.com/?b=2&a=1", policy=p).url == "https://e.com/?b=2&a=1"

    def test_empty_pairs_dropped(self):
        assert norm("https://e.com/?&&a=1&").url == "https://e.com/?a=1"

    def test_blank_value_preserved(self):
        assert norm("https://e.com/?a=&b").url == "https://e.com/?a=&b"

    def test_percent_encoding_normalised(self):
        assert norm("https://e.com/?q=%7e%c3%a9").url == "https://e.com/?q=~%C3%A9"
        assert norm("https://e.com/?q=a b").url == "https://e.com/?q=a%20b"

    def test_plus_is_preserved(self):
        assert norm("https://e.com/?q=a+b").url == "https://e.com/?q=a+b"

    def test_brackets_encoded_consistently(self):
        assert norm("https://e.com/?a[]=1").url == norm("https://e.com/?a%5B%5D=1").url

    @pytest.mark.parametrize(
        "raw",
        [
            "utm_source=x", "UTM_Medium=x", "utm_campaign=x&utm_term=y", "fbclid=abc", "gclid=1",
            "msclkid=1", "mc_cid=1&mc_eid=2", "_hsenc=1", "PHPSESSID=abc", "jsessionid=abc",
            "pk_campaign=x", "igshid=1", "yclid=1", "_ga=1.2", "mkt_tok=abc",
        ],
    )
    def test_tracking_params_removed(self, raw):
        assert norm(f"https://e.com/p?{raw}").url == "https://e.com/p"

    def test_tracking_removed_but_real_params_kept(self):
        assert norm("https://e.com/p?id=7&utm_source=n&fbclid=z").url == "https://e.com/p?id=7"

    @pytest.mark.parametrize("name", ["ref", "source", "campaign", "sid", "id", "page", "q", "p", "lang"])
    def test_generic_names_not_stripped(self, name):
        assert norm(f"https://e.com/p?{name}=1").url == f"https://e.com/p?{name}=1"

    def test_encoded_tracking_name_removed(self):
        assert norm("https://e.com/p?utm%5Fsource=x").url == "https://e.com/p"

    def test_extra_tracking_params_configurable(self):
        p = UrlPolicy(extra_tracking_params=frozenset({"ref"}))
        assert norm("https://e.com/p?ref=1&id=2", policy=p).url == "https://e.com/p?id=2"

    def test_tracking_removal_can_be_disabled(self):
        p = UrlPolicy(strip_tracking_params=False)
        assert norm("https://e.com/p?utm_source=x", policy=p).url == "https://e.com/p?utm_source=x"


class TestRelative:
    BASE = "https://example.com/company/team/index.html?x=1#top"

    @pytest.mark.parametrize(
        "href,expected",
        [
            ("/contact", "https://example.com/contact"),
            ("bob", "https://example.com/company/team/bob"),
            ("../about", "https://example.com/company/about"),
            ("./", "https://example.com/company/team"),
            ("?page=2", "https://example.com/company/team/index.html?page=2"),
            ("#bio", "https://example.com/company/team/index.html?x=1"),
            ("//cdn.example.org/x", "https://cdn.example.org/x"),
            ("http://other.org/A", "http://other.org/A"),
            ("HTTPS://Other.ORG/A", "https://other.org/A"),
        ],
    )
    def test_resolution(self, href, expected):
        assert norm(href, self.BASE).url == expected

    @pytest.mark.parametrize(
        "href", ["mailto:a@b.com", "javascript:alert(1)", "tel:+123", "data:text/html,x", "ftp://a.com/x", "file:///etc/passwd"]
    )
    def test_non_http_schemes_rejected(self, href):
        rejected(href, RejectReason.UNSUPPORTED_SCHEME, base=self.BASE)

    def test_relative_without_base_rejected(self):
        rejected("/just/a/path", RejectReason.INVALID_URL)
        rejected("example.com/x", RejectReason.INVALID_URL)

    def test_default_scheme_for_seeds(self):
        assert norm("example.com/team", default_scheme="https").url == "https://example.com/team"
        assert norm("//example.com/x", default_scheme="https").url == "https://example.com/x"
        assert norm("http://example.com", default_scheme="https").scheme == "http"
        rejected("mailto:a@b.com", RejectReason.UNSUPPORTED_SCHEME, default_scheme="https")


class TestRejections:
    def test_empty(self):
        rejected("", RejectReason.EMPTY)
        rejected("   \t\n ", RejectReason.EMPTY)

    def test_not_a_string(self):
        rejected(None, RejectReason.INVALID_URL)  # type: ignore[arg-type]

    def test_too_long(self):
        rejected("https://e.com/" + "a" * 3000, RejectReason.TOO_LONG)

    def test_length_checked_after_encoding(self):
        p = UrlPolicy(max_url_length=100)
        rejected("https://e.com/" + "€" * 20, RejectReason.TOO_LONG, policy=p)

    @pytest.mark.parametrize("raw", ["https://e.com/a\x00b", "https://e.com/a\x07b", "https://e.com/%00"])
    def test_control_and_null_bytes(self, raw):
        rejected(raw, RejectReason.INVALID_URL)

    @pytest.mark.parametrize(
        "raw",
        ["https://user@example.com/", "https://user:pw@example.com/", "https://example.com@evil.com/", "https://:@example.com/"],
    )
    def test_credentials(self, raw):
        rejected(raw, RejectReason.CREDENTIALS_IN_URL)

    def test_backslash_cannot_smuggle_a_host(self):
        # A browser reads this as host=good.example, path=/@evil.example/ (no credentials).
        n = norm("https://good.example\\@evil.example/")
        assert n.host == "good.example"
        assert n.path == "/@evil.example"

    @pytest.mark.parametrize("raw", ["https:///path", "https://", "http://:80/"])
    def test_missing_host(self, raw):
        rejected(raw, RejectReason.INVALID_HOST)

    @pytest.mark.parametrize("raw", ["https://exa mple.com/", "https://exa_mple!.com/", "https://a..b.com/", "https://-.com/x/y/%"])
    def test_bad_hosts(self, raw):
        with pytest.raises(UrlRejected) as info:
            norm(raw)
        assert info.value.reason in (RejectReason.INVALID_HOST, RejectReason.INVALID_URL)

    def test_overlong_label_and_host(self):
        rejected(f"https://{'a' * 64}.com/", RejectReason.INVALID_HOST)
        rejected("https://" + ".".join(["a" * 60] * 5) + ".com/", RejectReason.INVALID_HOST)

    @pytest.mark.parametrize("port", ["0", "65536", "99999", "abc", "-1", "1e3"])
    def test_invalid_ports(self, port):
        p = UrlPolicy(allowed_ports=None)
        with pytest.raises(UrlRejected) as info:
            norm(f"https://e.com:{port}/", policy=p)
        assert info.value.reason == RejectReason.INVALID_PORT

    def test_empty_port_ok(self):
        assert norm("https://e.com:/x").url == "https://e.com/x"

    def test_unterminated_ipv6(self):
        rejected("https://[::1/", RejectReason.INVALID_URL)

    def test_lone_surrogate_rejected(self):
        rejected("https://e.com/\ud800", RejectReason.INVALID_URL)


class TestPorts:
    def test_non_default_port_rejected_by_default(self):
        rejected("https://e.com:8443/", RejectReason.PORT_NOT_ALLOWED)
        rejected("http://e.com:22/", RejectReason.PORT_NOT_ALLOWED)

    def test_port_443_on_http_is_kept_as_an_explicit_port(self):
        n = norm("http://e.com:443/")
        assert n.url == "http://e.com:443/" and n.port == 443
        rejected("http://e.com:443/", RejectReason.PORT_NOT_ALLOWED, policy=UrlPolicy(allowed_ports=frozenset({80})))

    def test_hyphen_edges_rejected(self):
        rejected("https://-a.com/", RejectReason.INVALID_HOST)
        rejected("https://a-.com/", RejectReason.INVALID_HOST)

    def test_allowed_ports_configurable(self):
        p = UrlPolicy(allowed_ports=frozenset({80, 443, 8080}))
        assert norm("http://e.com:8080/x", policy=p).url == "http://e.com:8080/x"
        assert norm("http://e.com:8080/x", policy=p).port == 8080

    def test_any_port(self):
        assert norm("http://e.com:9999/", policy=UrlPolicy(allowed_ports=None)).port == 9999


class TestHostForms:
    def test_idn_to_punycode(self):
        assert norm("https://exämple.com/").host == "xn--exmple-cua.com"
        assert norm("https://EXÄMPLE.com/").host == "xn--exmple-cua.com"
        assert norm("https://xn--exmple-cua.com/").host == "xn--exmple-cua.com"

    def test_fullwidth_and_ideographic_dot(self):
        assert norm("https://ｅｘａｍｐｌｅ。ｃｏｍ/").host == "example.com"

    def test_trailing_dot_removed(self):
        assert norm("https://example.com./x").url == "https://example.com/x"

    def test_percent_encoded_host_decoded(self):
        assert norm("https://%65xample.com/").host == "example.com"

    @pytest.mark.parametrize(
        "host,expected",
        [
            ("2130706433", "127.0.0.1"),
            ("0x7f000001", "127.0.0.1"),
            ("0x7f.0.0.1", "127.0.0.1"),
            ("0177.0.0.1", "127.0.0.1"),
            ("127.1", "127.0.0.1"),
            ("127.0.1", "127.0.0.1"),
            ("017700000001", "127.0.0.1"),
            ("0", "0.0.0.0"),
            ("0x", "0.0.0.0"),
            ("3232235777", "192.168.1.1"),
            ("192.168.257", "192.168.1.1"),
            ("8.8.8.8.", "8.8.8.8"),
            ("１２７.0.0.1", "127.0.0.1"),
        ],
    )
    def test_ipv4_alternative_forms_canonicalised(self, host, expected):
        assert norm(f"http://{host}/").host == expected

    @pytest.mark.parametrize("host", ["256.1.1.1", "1.2.3.4.5", "0xg1.0.0.1", "089.0.0.1", "4294967296", "1.2.3.256", "example.123"])
    def test_invalid_numeric_hosts(self, host):
        rejected(f"http://{host}/", RejectReason.INVALID_HOST)

    def test_numeric_tld_like_names_are_not_ips_unless_all_numeric(self):
        assert norm("http://123abc.com/").host == "123abc.com"

    def test_ipv6_literals(self):
        assert norm("http://[::1]/").host == "[::1]"
        assert norm("http://[2001:DB8:0:0:0:0:0:1]/").host == "[2001:db8::1]"
        assert norm("http://[::ffff:127.0.0.1]/").host == "[::ffff:7f00:1]"

    def test_ipv6_with_port(self):
        p = UrlPolicy(allowed_ports=None)
        n = norm("http://[2001:db8::1]:8080/x", policy=p)
        assert n.url == "http://[2001:db8::1]:8080/x"
        assert n.port == 8080

    @pytest.mark.parametrize("raw", ["http://[::1%25eth0]/", "http://[zzz]/", "http://[::1]x/", "http://[::1]:99999/"])
    def test_bad_ipv6(self, raw):
        with pytest.raises(UrlRejected):
            norm(raw, policy=UrlPolicy(allowed_ports=None))


class TestIdempotence:
    URLS = [
        "HTTP://Example.COM:80/a/./b/../c//d/?utm_source=x&b=2&a=1#frag",
        "https://exämple.com/ü?q=a b&z=%7e",
        "http://0x7f.1/%2e%2e/x/",
        "https://e.com/a;jsessionid=1/b?x=%41",
        "https://e.com/?a[]=1&a[]=2",
        "http://[2001:DB8::1]/x",
    ]

    @pytest.mark.parametrize("raw", URLS)
    def test_normalising_twice_is_stable(self, raw):
        p = UrlPolicy(allowed_ports=None)
        first = norm(raw, policy=p)
        assert norm(first.url, policy=p).url == first.url

    @pytest.mark.parametrize("policy", [UrlPolicy(trailing_slash="keep"), UrlPolicy(collapse_slashes=False), UrlPolicy(sort_query=False)])
    def test_idempotent_under_policy_variants(self, policy):
        for raw in self.URLS:
            p = policy.with_changes(allowed_ports=None)
            first = norm(raw, policy=p)
            assert norm(first.url, policy=p).url == first.url


def test_normalized_url_properties():
    n = norm("https://example.com/a/b%20c/?x=1&y=%C3%A9&x=2")
    assert n.scheme == "https"
    assert n.host == "example.com"
    assert n.port is None and n.effective_port == 443
    assert n.segments == ("a", "b%20c")
    assert n.params == (("x", "1"), ("x", "2"), ("y", "é"))
