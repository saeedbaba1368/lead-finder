"""Phase 9.1: canonical website / domain identity (pure; no network, no database)."""

from __future__ import annotations

import dataclasses
import json
from urllib.parse import urlsplit

import pytest

from app.crawler import parse_html
from app.leads import BusinessLead, LeadDataError, extract_identity
from app.leads import identity as ident
from app.urls import DomainIdentity, UrlRejected, canonical_domain, domain_identity, normalize_url
from app.urls.domain_identity import WEBSITE_POLICY, identity_of_host
from app.urls.errors import RejectReason


def website(value: str) -> str:
    return domain_identity(value).website


def domain(value: str, **kwargs) -> str:
    return domain_identity(value, **kwargs).domain


# ---------------------------------------------------------------- scheme
@pytest.mark.parametrize("value", ["HTTPS://Acme.example/", "Https://acme.example", "https://acme.example:443/"])
def test_scheme_is_lower_cased_and_kept(value):
    got = domain_identity(value)
    assert (got.scheme, got.website) == ("https", "https://acme.example/")


def test_http_is_not_upgraded_but_has_the_same_domain():
    plain, secure = domain_identity("http://acme.example/a"), domain_identity("https://acme.example/a")
    assert plain.scheme == "http" and plain.website == "http://acme.example/a"
    assert plain.domain == secure.domain == "acme.example"


def test_upper_case_scheme_is_not_mistaken_for_a_host():
    # normalize_url(default_scheme=...) alone turns `HTTP://ACME.EXAMPLE/a` into host `http`; the identity must not.
    assert domain_identity("HTTP://ACME.EXAMPLE/a").website == "http://acme.example/a"
    assert domain_identity("HTTP://ACME.EXAMPLE/a", default_scheme="https").host == "acme.example"


@pytest.mark.parametrize("value", ["acme.example", "ACME.example/", "//acme.example", "  acme.example\n"])
def test_scheme_less_value_gets_the_default_scheme(value):
    assert website(value) == "https://acme.example/"


def test_custom_default_scheme_and_strict_mode():
    assert domain_identity("acme.example", default_scheme="http").website == "http://acme.example/"
    with pytest.raises(UrlRejected):
        domain_identity("acme.example", default_scheme=None)
    with pytest.raises(UrlRejected):
        domain_identity("//acme.example", default_scheme=None)


@pytest.mark.parametrize(
    "value",
    ["ftp://acme.example", "mailto:info@acme.example", "javascript:alert(1)", "tel:+123", "file:///etc/passwd", "data:text/html,x"],
)
def test_only_http_and_https_exist(value):
    with pytest.raises(UrlRejected):
        domain_identity(value)


# ---------------------------------------------------------------- hostname
def test_hostname_is_lower_cased():
    got = domain_identity("https://WWW.AcMe.EXAMPLE/Team")
    assert got.host == "www.acme.example" and got.domain == "acme.example"
    assert got.path == "/Team"  # only the host is case-folded, never the path


@pytest.mark.parametrize("value", ["https://acme.example.", "https://ACME.example./", "acme.example."])
def test_trailing_host_dot_is_removed(value):
    got = domain_identity(value)
    assert (got.host, got.domain) == ("acme.example", "acme.example")


def test_idn_hosts_are_punycode_and_spellings_agree():
    spellings = ["https://münchen.de", "https://MÜNCHEN.de/", "https://xn--mnchen-3ya.de", "https://WWW.münchen.de.", "münchen.de"]
    assert {domain(v) for v in spellings} == {"xn--mnchen-3ya.de"}
    assert domain_identity("https://www.münchen.de/x").host == "www.xn--mnchen-3ya.de"


def test_persian_idn_domain():
    assert domain("https://پارس.ir/") == domain("https://xn--mgbug11a.ir/") == "xn--mgbug11a.ir"


@pytest.mark.parametrize("value", ["https://acme_.exam ple", "https://-acme.example", "https://a..example", "https://", "https:///x"])
def test_invalid_hosts_are_rejected(value):
    with pytest.raises(UrlRejected):
        domain_identity(value)


# ---------------------------------------------------------------- ports
@pytest.mark.parametrize("value", ["http://acme.example:80/", "https://acme.example:443/", "HTTP://acme.example:80"])
def test_default_ports_are_removed(value):
    got = domain_identity(value)
    assert got.port is None and ":" not in got.website.split("//", 1)[1]


def test_non_default_ports_are_kept_in_the_website_but_not_in_the_domain():
    got = domain_identity("https://acme.example:8443/a")
    assert (got.port, got.website, got.domain) == (8443, "https://acme.example:8443/a", "acme.example")
    # a port that is the default of the *other* scheme is not a default
    assert domain_identity("http://acme.example:443/").website == "http://acme.example:443/"


@pytest.mark.parametrize("value", ["https://acme.example:0/", "https://acme.example:65536/", "https://acme.example:abc/"])
def test_invalid_ports_are_rejected(value):
    with pytest.raises(UrlRejected) as exc:
        domain_identity(value)
    assert exc.value.reason is RejectReason.INVALID_PORT


def test_the_crawl_port_policy_is_not_applied():
    assert domain_identity("https://acme.example:9999/").port == 9999


# ---------------------------------------------------------------- fragments, query, tracking
def test_fragments_are_removed():
    assert website("https://acme.example/team#staff") == "https://acme.example/team"
    assert website("https://acme.example/#top") == "https://acme.example/"
    assert website("https://acme.example#a#b") == "https://acme.example/"


def test_tracking_parameters_go_and_meaningful_parameters_stay():
    assert website("https://acme.example/p?utm_source=x&id=7&gclid=1") == "https://acme.example/p?id=7"
    assert website("https://acme.example/p?b=2&a=1") == "https://acme.example/p?a=1&b=2"


# ---------------------------------------------------------------- www
@pytest.mark.parametrize(
    "host",
    ["acme.example", "www.acme.example", "WWW.ACME.EXAMPLE", "www2.acme.example", "www.www.acme.example", "shop.www.acme.example"],
)
def test_www_never_changes_the_domain_of_a_normal_site(host):
    assert domain(f"https://{host}/") == "acme.example"


def test_www_stays_in_the_website_url_because_that_is_the_address():
    got = domain_identity("https://www.acme.example/team")
    assert got.website == "https://www.acme.example/team" and got.host == "www.acme.example"
    assert got.domain == "acme.example"


@pytest.mark.parametrize("suffix", ["co.uk", "github.io", "com.au", "blogspot.com"])
def test_www_of_a_public_suffix_agrees_with_the_suffix(suffix):
    assert domain(f"https://www.{suffix}/") == domain(f"https://{suffix}/") == suffix


def test_a_real_domain_that_starts_with_www_is_left_alone():
    assert domain("https://www.com/") == "www.com"
    assert domain("https://www.example.com/") == "example.com"


def test_www_under_a_multi_label_suffix_site():
    assert domain("https://www.acme.co.uk/") == domain("https://acme.co.uk/") == "acme.co.uk"
    assert domain("https://shop.acme.co.uk/") == "acme.co.uk"


def test_hosting_platform_tenants_stay_separate():
    assert domain("https://a.github.io/") == "a.github.io" != domain("https://b.github.io/")
    assert domain("https://www.a.github.io/") == "a.github.io"


def test_extra_public_suffixes_are_opt_in():
    assert domain("https://shop.platform.test/") == "platform.test"
    assert domain("https://shop.platform.test/", extra_suffixes=frozenset({"platform.test"})) == "shop.platform.test"
    assert domain("https://www.platform.test/", extra_suffixes=frozenset({"platform.test"})) == "platform.test"


# ---------------------------------------------------------------- paths
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://acme.example", "https://acme.example/"),
        ("https://acme.example/en/team", "https://acme.example/en/team"),
        ("https://acme.example/en/team/", "https://acme.example/en/team"),
        ("https://acme.example/a/./b/../c", "https://acme.example/a/c"),
        ("https://acme.example//a///b", "https://acme.example/a/b"),
        ("https://acme.example/About-Us", "https://acme.example/About-Us"),
        ("https://acme.example/index.html", "https://acme.example/index.html"),
        ("https://acme.example/%7Euser/%e2%82%ac", "https://acme.example/~user/%E2%82%AC"),
        ("https://acme.example/a;jsessionid=XYZ/b", "https://acme.example/a/b"),
    ],
)
def test_meaningful_paths_are_preserved_and_normalised(value, expected):
    got = domain_identity(value)
    assert got.website == expected
    assert got.path == urlsplit(expected).path


def test_a_path_never_changes_the_domain():
    assert {domain(f"https://acme.example{p}") for p in ["", "/", "/en", "/en/team/", "/x?y=1#z"]} == {"acme.example"}


# ---------------------------------------------------------------- IP literals and single labels
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1/", "127.0.0.1"),
        ("http://127.1/", "127.0.0.1"),
        ("http://0x7f000001/", "127.0.0.1"),
        ("http://[::1]/", "::1"),
        ("http://[2001:DB8:0:0::1]:80/", "2001:db8::1"),
        ("::1", "::1"),
        ("2001:DB8::1", "2001:db8::1"),
        ("http://localhost:8000/", "localhost"),
        ("localhost", "localhost"),
    ],
)
def test_ip_literals_and_single_labels_are_their_own_identity(value, expected):
    assert domain(value) == expected


def test_ipv6_host_stays_bracketed_in_the_url_but_not_in_the_domain():
    got = domain_identity("http://[2001:db8::1]:8080/a")
    assert (got.host, got.website, got.domain) == ("[2001:db8::1]", "http://[2001:db8::1]:8080/a", "2001:db8::1")


def test_public_suffix_host_is_its_own_identity():
    assert domain("https://co.uk/") == "co.uk"
    assert identity_of_host("github.io") == "github.io"


# ---------------------------------------------------------------- rejected values
@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_values_are_rejected(value):
    with pytest.raises(UrlRejected) as exc:
        domain_identity(value)
    assert exc.value.reason is RejectReason.EMPTY


@pytest.mark.parametrize("value", [None, 5, b"https://acme.example", ["https://acme.example"]])
def test_non_strings_are_rejected(value):
    with pytest.raises(UrlRejected):
        domain_identity(value)


def test_credentials_are_rejected():
    with pytest.raises(UrlRejected) as exc:
        domain_identity("https://user:secret@acme.example/")
    assert exc.value.reason is RejectReason.CREDENTIALS_IN_URL
    with pytest.raises(UrlRejected):
        domain_identity("acme.example@evil.example")


def test_overlong_values_are_rejected():
    with pytest.raises(UrlRejected):
        domain_identity("https://acme.example/" + "a" * 3000)


# ---------------------------------------------------------------- determinism
SPELLINGS_OF_ONE_SITE = [
    "https://acme.example",
    "https://acme.example/",
    "HTTPS://ACME.EXAMPLE/",
    "https://www.acme.example",
    "http://www.acme.example:80/",
    "https://acme.example:443/#team",
    "https://acme.example./",
    "acme.example",
    "//www.ACME.example",
    "https://acme.example/?utm_campaign=x",
    "https://shop.acme.example/cart",
]


def test_every_spelling_of_one_site_has_one_domain():
    assert {canonical_domain(v) for v in SPELLINGS_OF_ONE_SITE} == {"acme.example"}


def test_result_is_deterministic_and_idempotent():
    for value in SPELLINGS_OF_ONE_SITE + ["https://münchen.de/Team/?b=2&a=1#x", "http://[::1]:8080/", "https://www.co.uk/x"]:
        first = domain_identity(value)
        assert domain_identity(value) == first
        again = domain_identity(first.website, default_scheme=None)
        assert again == first  # canonical output is a fixed point
        assert canonical_domain(first.domain) == first.domain  # so is the identity key itself


def test_website_agrees_with_normalize_url():
    for value in (v for v in SPELLINGS_OF_ONE_SITE if "://" in v):
        assert domain_identity(value).website == normalize_url(value, policy=WEBSITE_POLICY).url


def test_identity_is_immutable_and_serialisable():
    got = domain_identity("https://www.acme.example:8443/en")
    assert isinstance(got, DomainIdentity)
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.domain = "other.example"  # type: ignore[misc]
    assert got.to_dict() == {
        "domain": "acme.example", "host": "www.acme.example", "website": "https://www.acme.example:8443/en",
        "scheme": "https", "port": 8443, "path": "/en",
    }
    json.dumps(got.to_dict())


def test_existing_url_normalisation_is_unchanged():
    assert normalize_url("HTTP://WWW.Example.COM:80/Team/?utm_source=x#frag").url == "http://www.example.com/Team"
    assert normalize_url("https://example.com:443").url == "https://example.com/"
    assert normalize_url("example.com/team", default_scheme="https").url == "https://example.com/team"
    with pytest.raises(UrlRejected):
        normalize_url("example.com/team")  # still strict without a default scheme


# ---------------------------------------------------------------- Lead records use it
@pytest.mark.parametrize("value", SPELLINGS_OF_ONE_SITE[:7])
def test_lead_domain_is_the_canonical_domain(value):
    lead = BusinessLead(website=value)
    assert lead.domain == "acme.example"
    assert lead.website == domain_identity(value).website


def test_lead_website_is_still_strict_about_the_scheme():
    with pytest.raises(LeadDataError):
        BusinessLead(website="acme.example")


@pytest.mark.parametrize(
    "explicit",
    ["acme.example", "ACME.example", "acme.example.", "www.acme.example", "WWW.Acme.Example", "https://www.acme.example/", "acme.example:80"],
)
def test_explicit_domain_in_any_spelling_of_the_same_identity_agrees(explicit):
    assert BusinessLead(website="https://www.acme.example/team", domain=explicit).domain == "acme.example"


@pytest.mark.parametrize("explicit", ["other.example", "acme.example.evil.test", "acme.exampl", "evil.test/acme.example", "not a domain"])
def test_explicit_domain_of_another_identity_is_still_rejected(explicit):
    with pytest.raises(LeadDataError, match="does not match"):
        BusinessLead(website="https://www.acme.example/", domain=explicit)


def test_ip_lead_round_trips_through_serialisation():
    lead = BusinessLead(website="http://[2001:DB8::1]:8080/")
    assert lead.domain == "2001:db8::1"
    assert BusinessLead.from_dict(lead.to_dict()) == lead
    assert BusinessLead.from_json(lead.to_json()) == lead


def test_co_uk_www_lead_agrees_with_its_bare_suffix():
    assert BusinessLead(website="https://www.co.uk/").domain == BusinessLead(website="https://co.uk/").domain == "co.uk"


def test_domain_of_uses_the_identity_and_raises_lead_data_error():
    assert ident.domain_of("https://WWW.Shop.Acme.co.uk:443/x") == "acme.co.uk"
    assert ident.domain_of("https://münchen.de/") == "xn--mnchen-3ya.de"
    with pytest.raises(LeadDataError):
        ident.domain_of("")
    with pytest.raises(LeadDataError):
        ident.domain_of("acme.example")  # strict: a website needs its scheme


def test_extract_identity_domain_is_canonical_for_every_source_spelling():
    parsed = parse_html("<html><head><title>Acme</title></head></html>", "https://acme.example/")
    for source in ["https://acme.example/", "HTTPS://WWW.ACME.EXAMPLE:443/team#x", "http://acme.example./about"]:
        assert extract_identity(parsed, source_url=source).domain == "acme.example"
    assert extract_identity(parsed, source_url="https://x.example/", website="https://WWW.Acme.example/en/").domain == "acme.example"


# ---------------------------------------------------------------- JSON-LD site check uses the same identity
def _ld(**fields) -> str:
    node = {"@context": "https://schema.org", "@type": "LocalBusiness", **fields}
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


def _name(record_url: str | None, site: str = "https://acme.example/") -> tuple[str | None, str | None]:
    fields = {"name": "Record Name"} | ({"url": record_url} if record_url is not None else {})
    head = f'<meta property="og:site_name" content="OG Name">{_ld(**fields)}'
    parsed = parse_html(f"<html><head>{head}</head><body></body></html>", site)
    found = extract_identity(parsed, source_url=site)
    return found.business_name, found.name_source


@pytest.mark.parametrize(
    "record_url",
    ["https://acme.example", "https://WWW.ACME.example/contact", "http://acme.example:80/", "acme.example", "https://acme.example./", "https://shop.acme.example/"],
)
def test_json_ld_record_of_this_site_matches_in_any_spelling(record_url):
    assert _name(record_url) == ("Record Name", "json_ld")


def test_json_ld_record_url_in_unicode_or_trailing_dot_form_matches():
    # these were wrongly treated as "another site" while the check had its own host logic
    assert _name("https://münchen.de", site="https://xn--mnchen-3ya.de/") == ("Record Name", "json_ld")
    assert _name("https://example.com./", site="https://example.com/") == ("Record Name", "json_ld")


@pytest.mark.parametrize("record_url", ["https://other.example", "https://acme.example.evil.test/", "https://acme.exampl/", "mailto:a@acme.example", "not a url", "https://"])
def test_json_ld_record_of_another_site_or_with_an_unusable_url_is_skipped(record_url):
    assert _name(record_url) == ("OG Name", "og_site_name")


def test_json_ld_record_without_url_is_neutral():
    assert _name(None) == ("Record Name", "json_ld")
