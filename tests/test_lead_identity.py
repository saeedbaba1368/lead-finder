"""Phase 9.2: deterministic Lead identity (pure, except one SQLite-in-memory persistence check)."""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.crawler import PhoneNumber, parse_html
from app.leads import (
    BusinessLead,
    LeadDataError,
    LeadIdentity,
    aggregate_leads,
    aggregate_parsed_pages,
    extract_identity,
    lead_identity,
)
from app.repositories import LeadRepository
from app.urls import domain_identity

ROOT = Path(__file__).resolve().parent.parent

# Digests computed independently with `printf 'lead:v1:<domain>' | sha256sum`.
GOLDEN = {
    "acme.example": "0c4eb381f9acc6b11f7aa29aeef41da9c69dfbe8b7a4a76134125fffb92a38f0",
    "xn--mnchen-3ya.de": "d863e135e61c0b158bc5b1033ae3ed19c6aacba9cb6a8ffbff919a1efed3e219",
    "xn--mgbug11a.ir": "5cbcba719f1eaafdaf78780c9ddc1ea06323093f7c3b9402e305c47e98e1758f",
}


def ident(value) -> LeadIdentity:
    return lead_identity(value)


# ---------------------------------------------------------------- shape
def test_identity_fields_are_derived_from_the_domain():
    got = ident("https://acme.example/")
    assert got.domain == "acme.example"
    assert got.key == "lead:v1:acme.example"
    assert got.digest == GOLDEN["acme.example"]
    assert got.version == 1 and str(got) == got.key
    assert re.fullmatch(r"[0-9a-f]{64}", got.digest)


def test_golden_values_are_fixed():
    assert ident("acme.example").digest == GOLDEN["acme.example"]
    assert ident("https://münchen.de/").digest == GOLDEN["xn--mnchen-3ya.de"]
    assert ident("https://پارس.ir/").digest == GOLDEN["xn--mgbug11a.ir"]


def test_identity_is_immutable_and_hashable():
    got = ident("acme.example")
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.domain = "other.example"  # type: ignore[misc]
    assert len({got, ident("https://www.acme.example/x"), ident("HTTP://ACME.example")}) == 1
    assert {got: 1}[ident("acme.example.")] == 1


# ---------------------------------------------------------------- same domain
def test_same_domain_same_identity():
    a = BusinessLead(website="https://acme.example/", business_name="Acme Plumbing", emails=["a@acme.example"])
    b = BusinessLead(website="https://acme.example/", business_name="Totally Different Name", description="x")
    assert a.identity == b.identity and a.identity.key == b.identity.key and a.identity.digest == b.identity.digest
    # lead content never takes part
    assert ident(a) == ident(b) == ident("acme.example")


# ---------------------------------------------------------------- different paths
@pytest.mark.parametrize("path", ["", "/", "/en", "/en/team/", "/contact?x=1&utm_source=y", "/a/b/c#frag", "/index.html", "/Team"])
def test_different_paths_on_the_same_domain_share_one_identity(path):
    assert ident(f"https://acme.example{path}") == ident("https://acme.example/")
    assert BusinessLead(website=f"https://acme.example{path}").identity == ident("acme.example")


def test_source_url_does_not_change_the_identity():
    home = BusinessLead(website="https://acme.example/", source_url="https://acme.example/")
    inner = BusinessLead(website="https://acme.example/", source_url="https://acme.example/contact/us")
    assert home.identity == inner.identity


# ---------------------------------------------------------------- http / https
@pytest.mark.parametrize("url", ["http://acme.example", "https://acme.example", "HTTP://ACME.EXAMPLE:80/", "https://acme.example:443/", "http://acme.example:8080/x", "https://acme.example:8443/x"])
def test_http_https_and_ports_are_equivalent(url):
    assert ident(url) == ident("https://acme.example")


def test_http_and_https_leads_have_one_identity_but_keep_their_own_website():
    plain, secure = BusinessLead(website="http://acme.example/"), BusinessLead(website="https://acme.example/")
    assert plain.website != secure.website
    assert plain.identity == secure.identity


# ---------------------------------------------------------------- www (project rule: www is never part of an identity)
@pytest.mark.parametrize("host", ["www.acme.example", "WWW.ACME.EXAMPLE", "www2.acme.example", "www.www.acme.example", "shop.acme.example"])
def test_www_and_non_www_are_the_same_site(host):
    assert ident(f"https://{host}/") == ident("https://acme.example/")


def test_www_rule_for_public_suffixes_and_real_www_domains():
    assert ident("https://www.acme.co.uk") == ident("https://acme.co.uk")
    assert ident("https://www.co.uk") == ident("https://co.uk")
    assert ident("https://www.com").domain == "www.com"  # a real domain, not a www host
    assert ident("https://www.com") != ident("https://example.com")


def test_www_and_non_www_leads_agree_with_the_url_engine_rule():
    # the same rule the crawler uses to treat www.X and X as one site (`dedupe_ignore_www`, scope)
    for a, b in [("https://www.acme.example/", "https://acme.example/"), ("http://www.acme.co.uk/x", "https://acme.co.uk/y")]:
        assert domain_identity(a).domain == domain_identity(b).domain
        assert ident(a) == ident(b)


# ---------------------------------------------------------------- different domains
def test_different_domains_have_different_identities():
    urls = [
        "https://acme.example/", "https://acme.example.org/", "https://acme.co.uk/", "https://acme.com/",
        "https://other.example/", "https://a.github.io/", "https://b.github.io/", "http://127.0.0.1/", "http://[::1]/", "http://localhost/",
        "https://xn--mnchen-3ya.de/", "https://acme-example.example/",
    ]
    identities = [ident(u) for u in urls]
    assert len({i.domain for i in identities}) == len(urls)
    assert len({i.key for i in identities}) == len(urls)
    assert len({i.digest for i in identities}) == len(urls)


def test_no_fuzzy_matching():
    near_misses = ["acme.example", "acme.exampel", "acme.exampl", "acmee.example", "acme-plumbing.example", "acmeplumbing.example", "ACME.example.com"]
    assert len({ident(v).digest for v in near_misses}) == len(near_misses)
    # same business name on two domains: still two identities
    one = BusinessLead(website="https://acme.example/", business_name="Acme Plumbing")
    two = BusinessLead(website="https://acme-plumbing.example/", business_name="Acme Plumbing")
    assert one.identity != two.identity


def test_tenants_of_a_hosting_platform_are_separate_leads():
    assert ident("https://a.github.io/") != ident("https://b.github.io/")
    assert ident("https://www.a.github.io/") == ident("https://a.github.io/")


# ---------------------------------------------------------------- repeated generation / sources
def test_repeated_generation_is_identical():
    first = ident("https://www.Acme.example:443/Team/?b=2&a=1#x")
    for _ in range(500):
        again = ident("https://www.Acme.example:443/Team/?b=2&a=1#x")
        assert again == first and again.to_json() == first.to_json() and again.digest == first.digest


def test_every_kind_of_source_gives_the_same_identity():
    parsed = parse_html("<html><head><title>Acme</title></head></html>", "https://acme.example/")
    lead = BusinessLead(website="https://www.acme.example/team")
    sources = [
        "https://acme.example", "acme.example", "WWW.Acme.Example.", lead, domain_identity("https://shop.acme.example/x"),
        extract_identity(parsed, source_url="https://acme.example/contact"), LeadIdentity("acme.example"), ident("acme.example"),
    ]
    assert {ident(s) for s in sources} == {ident("acme.example")}
    assert ident(ident("acme.example")) == ident("acme.example")  # passing an identity back returns it


@pytest.mark.parametrize("bad", [None, 5, b"acme.example", "", "   ", "ftp://acme.example", "mailto:a@acme.example", "https://user:pw@acme.example", "a b", object(), ["acme.example"]])
def test_unusable_sources_raise_lead_data_error(bad):
    with pytest.raises(LeadDataError):
        lead_identity(bad)


# ---------------------------------------------------------------- independence of page order
def _page_leads() -> list[BusinessLead]:
    return [
        BusinessLead(website="https://www.acme.example/", source_url="https://www.acme.example/", emails=["a@acme.example"]),
        BusinessLead(website="http://acme.example/", source_url="http://acme.example/contact", phones=[PhoneNumber("+14155550199", "+1 415 555 0199")]),
        BusinessLead(website="https://shop.acme.example/", source_url="https://shop.acme.example/p/1", business_name="Acme Shop"),
        BusinessLead(website="https://acme.example:8443/x", source_url="https://acme.example:8443/x/y"),
    ]


def test_identity_does_not_depend_on_the_order_of_pages():
    leads = _page_leads()
    identities = {aggregate_leads(order, website="https://acme.example/").identity for order in itertools.permutations(leads)}
    assert identities == {ident("acme.example")}
    assert {lead.identity for lead in leads} == {ident("acme.example")}  # and so does every single page's lead


def test_identity_of_parsed_pages_is_independent_of_their_order():
    pages = [
        (parse_html("<html><head><title>Home | Acme</title></head><body>info@acme.example</body></html>", "https://acme.example/"), "https://acme.example/"),
        (parse_html("<html><head><title>Contact | Acme</title></head></html>", "https://www.acme.example/contact"), "https://www.acme.example/contact"),
        (parse_html("<html><head><title>Team | Acme</title></head></html>", "http://acme.example/team/"), "http://acme.example/team/"),
    ]
    results = {aggregate_parsed_pages(order).identity for order in itertools.permutations(pages)}
    assert len(results) == 1 and results.pop().domain == "acme.example"


# ---------------------------------------------------------------- stability across restarts
_SNIPPET = (
    "import json\n"
    "from app.leads import BusinessLead, lead_identity\n"
    "urls = ['https://WWW.Acme.example/a?b=1#c', 'http://münchen.de:80/', 'https://پارس.ir/x', 'http://[2001:db8::1]/', 'https://a.github.io/']\n"
    "print(json.dumps([lead_identity(u).to_dict() for u in urls] + [BusinessLead(website=urls[0]).identity.to_dict()], sort_keys=True))\n"
)


def test_identity_is_stable_across_processes_and_hash_seeds():
    outputs = set()
    for seed in ("0", "1", "42", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8", "APP_ENV": "test"}
        done = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True)
        outputs.add(done.stdout)
    assert len(outputs) == 1
    here = [lead_identity(u).to_dict() for u in ("https://WWW.Acme.example/a?b=1#c", "http://münchen.de:80/", "https://پارس.ir/x", "http://[2001:db8::1]/", "https://a.github.io/")]
    assert json.loads(outputs.pop())[:5] == here
    assert here[0]["digest"] == GOLDEN["acme.example"]


# ---------------------------------------------------------------- Unicode
@pytest.mark.parametrize("spelling", ["https://münchen.de", "https://MÜNCHEN.de/x", "https://www.münchen.de./", "https://xn--mnchen-3ya.de", "münchen.de"])
def test_unicode_and_punycode_spellings_agree(spelling):
    got = ident(spelling)
    assert got.domain == "xn--mnchen-3ya.de" and got.digest == GOLDEN["xn--mnchen-3ya.de"]


def test_identity_is_pure_ascii_whatever_the_input_script():
    for url in ["https://münchen.de", "https://پارس.ir", "https://пример.рф", "https://例え.jp", "https://ουζο.gr/ελληνικά"]:
        got = ident(url)
        assert got.key.isascii() and got.digest.isascii() and got.domain.isascii()
        assert LeadIdentity.from_json(got.to_json()) == got


def test_lead_with_persian_content_has_an_ascii_identity_independent_of_its_text():
    persian = BusinessLead(website="https://arman.example/", business_name="لوله‌کشی آرمان", description="خدمات تهران")
    english = BusinessLead(website="https://arman.example/", business_name="Arman Plumbing")
    assert persian.identity == english.identity and persian.identity.key.isascii()


# ---------------------------------------------------------------- serialisation
def test_json_and_dict_round_trip():
    got = ident("https://www.münchen.de/x")
    assert LeadIdentity.from_dict(got.to_dict()) == got
    assert LeadIdentity.from_json(got.to_json()) == got
    assert got.to_json() == json.dumps(got.to_dict(), sort_keys=True, separators=(",", ":"))
    assert got.to_json() == ident("münchen.de").to_json()  # canonical, byte-identical text
    assert json.loads(got.to_json()) == {"version": 1, "domain": "xn--mnchen-3ya.de", "key": "lead:v1:xn--mnchen-3ya.de", "digest": GOLDEN["xn--mnchen-3ya.de"]}


def _data(**changes):
    return {**ident("acme.example").to_dict(), **changes}


@pytest.mark.parametrize(
    "bad",
    [
        _data(digest="0" * 64), _data(key="lead:v1:other.example"), _data(version=2), _data(version=True), _data(version="1"),
        _data(domain="WWW.acme.example"), _data(domain="www.acme.example"), _data(domain="https://acme.example/"), _data(domain=""), _data(domain=None),
        {**_data(), "extra": 1}, {k: v for k, v in _data().items() if k != "digest"}, {}, [], None, "acme.example",
    ],
)
def test_tampered_or_malformed_identities_are_rejected(bad):
    with pytest.raises(LeadDataError):
        LeadIdentity.from_dict(bad)


@pytest.mark.parametrize("bad", ["", "not json", "[]", "null", "5", None, b"{}"])
def test_bad_json_is_rejected(bad):
    with pytest.raises(LeadDataError):
        LeadIdentity.from_json(bad)


@pytest.mark.parametrize("domain", ["Acme.example", "www.acme.example", "acme.example.", "xn--mnchen-3ya.de ", "münchen.de", "", "a b"])
def test_direct_construction_only_takes_a_canonical_domain(domain):
    with pytest.raises(LeadDataError):
        LeadIdentity(domain)


# ---------------------------------------------------------------- compatible with existing persistence
def test_identity_is_the_stored_domain_name(session):
    repo = LeadRepository(session)
    lead = BusinessLead(website="https://WWW.München.de/Team/?x=1#y", business_name="Müller GmbH", emails=["info@xn--mnchen-3ya.de"])
    saved = repo.save(lead).record
    session.flush()
    assert saved.domain.name == lead.identity.domain == "xn--mnchen-3ya.de"
    assert repo.get_by_domain(lead.identity.domain) is saved
    loaded = repo.load(lead.identity.domain)
    assert loaded is not None and loaded.identity == lead.identity
    assert ident(saved.domain.name) == lead.identity  # a stored row's domain name is itself a valid identity source


def test_identity_survives_a_store_and_reload_for_every_stored_lead(session):
    repo = LeadRepository(session)
    for website in ["https://acme.example/", "http://www.other.example:80/p", "https://پارس.ir/", "http://[2001:db8::1]:8080/"]:
        lead = BusinessLead(website=website)
        repo.save(lead)
        session.flush()
        assert {l.identity for l in repo.load_all() if l.domain == lead.domain} == {lead.identity}
        assert LeadIdentity.from_json(lead.identity.to_json()).domain == repo.get_by_domain(lead.identity.domain).domain.name
