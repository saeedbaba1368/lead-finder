"""Phase 8.2: deterministic business identity extraction (pure; one crawler test uses a loopback server)."""

from __future__ import annotations

import json

import pytest

from app.crawler import PageCategory, parse_html
from app.leads import BusinessIdentity, BusinessLead, LeadDataError, extract_identity
from app.leads import identity as ident
from tests.test_crawler_bfs import crawl, local_network  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML_HEADERS, server


def page(head: str = "", body: str = "", url: str = "https://acme.example/", lang: str = "en"):
    return parse_html(f'<html lang="{lang}"><head>{head}</head><body>{body}</body></html>', url)


def identify(head: str = "", body: str = "", url: str = "https://acme.example/", lang: str = "en", **kwargs):
    return extract_identity(page(head, body, url, lang), source_url=url, **kwargs)


def ld(**fields) -> str:
    node = {"@context": "https://schema.org", "@type": "LocalBusiness", **fields}
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


# ---------------------------------------------------------------- 1. JSON-LD business name
def test_json_ld_name_is_the_first_choice():
    idn = identify(f"<title>Welcome | Something Else</title>"
                   f'<meta property="og:site_name" content="OG Name">{ld(name="Acme Plumbing")}')
    assert (idn.business_name, idn.name_source) == ("Acme Plumbing", "json_ld")
    assert [c.source for c in idn.candidates][0] == "json_ld"


def test_json_ld_name_is_cleaned():
    assert identify(ld(name="  Acme \n  Plumbing  ")).business_name == "Acme Plumbing"


def test_person_records_are_not_business_names():
    person = '<script type="application/ld+json">{"@type":"Person","name":"Jane Doe"}</script>'
    idn = identify(f"<title>Acme Plumbing</title>{person}")
    assert idn.business_name == "Acme Plumbing" and idn.name_source == "title"


def test_json_ld_record_about_another_site_is_skipped():
    other = ld(name="Partner Corp", url="https://partner.example/")
    assert identify(f"<title>Acme</title>{other}").name_source == "title"


def test_json_ld_record_matching_the_site_is_preferred_over_url_less_ones():
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "name": "No Url Inc"},
        {"@type": "Organization", "name": "Acme Plumbing", "url": "https://www.acme.example/about"},
    ]}
    html = f'<script type="application/ld+json">{json.dumps(graph)}</script>'
    assert identify(html).business_name == "Acme Plumbing"


@pytest.mark.parametrize("bad", ["", "   ", "https://acme.example", "a@b.example", "x" * 121])
def test_unusable_json_ld_names_are_ignored(bad):
    assert identify(ld(name=bad)).business_name is None


# ---------------------------------------------------------------- 2. metadata business name
def test_open_graph_site_name_beats_the_title():
    idn = identify('<title>Contact us | Wrong Name</title><meta property="og:site_name" content="Acme Plumbing">')
    assert (idn.business_name, idn.name_source) == ("Acme Plumbing", "og_site_name")


def test_og_site_name_is_captured_by_the_parser():
    parsed = page('<meta property="og:site_name" content="  Acme  ">')
    assert parsed.metadata.og_site_name == "Acme" and parsed.to_dict()["metadata"]["og_site_name"] == "Acme"
    assert page().metadata.og_site_name is None


def test_og_title_brand_is_used_when_there_is_no_site_name():
    idn = identify('<meta property="og:title" content="Acme Plumbing | Plumbers in Springfield">')
    assert (idn.business_name, idn.name_source) == ("Acme Plumbing", "og_title")


def test_og_title_comes_before_title():
    idn = identify('<title>Other Name | Plumbers</title><meta property="og:title" content="Acme Plumbing | Plumbers">')
    assert (idn.business_name, idn.name_source) == ("Acme Plumbing", "og_title")
    assert [c.source for c in idn.candidates] == ["og_title", "title"] and idn.conflict


def test_generic_site_name_is_ignored():
    idn = identify('<title>Acme Plumbing</title><meta property="og:site_name" content="Home">')
    assert idn.name_source == "title"


# ---------------------------------------------------------------- 3. title fallback
@pytest.mark.parametrize("title,url,expected", [
    ("Acme Plumbing | Plumbers in Springfield", "https://acme.example/", "Acme Plumbing"),   # homepage: first segment
    ("Acme Plumbing - Home", "https://acme.example/", "Acme Plumbing"),                       # generic segment dropped
    ("Home | Acme Plumbing", "https://acme.example/", "Acme Plumbing"),
    ("Acme Plumbing", "https://acme.example/", "Acme Plumbing"),                              # single segment, homepage
    ("Contact us | Acme Plumbing", "https://acme.example/contact", "Acme Plumbing"),          # inner page: last segment
    ("Acme Plumbing – Contact", "https://acme.example/contact", "Acme Plumbing"),
    ("Pipe repair » Acme Plumbing", "https://acme.example/services/pipes", "Acme Plumbing"),
    ("Pipe repair: how it works | Acme Plumbing", "https://acme.example/services/pipes", "Acme Plumbing"),
])
def test_title_fallback(title, url, expected):
    idn = identify(f"<title>{title}</title>", url=url)
    assert (idn.business_name, idn.name_source) == (expected, "title")


def test_domain_matching_segment_wins_over_position():
    idn = identify("<title>Acme Plumbing | Best Plumbers in Town</title>", url="https://best-plumbers.example/contact")
    assert idn.business_name == "Best Plumbers in Town"
    idn = identify("<title>Pipe repair | Acme Plumbing</title>", url="https://pipe-repair.example/")
    assert idn.business_name == "Pipe repair"


def test_single_segment_title_of_an_inner_page_is_a_page_name_not_a_business():
    assert identify("<title>Our latest offers</title>", url="https://acme.example/offers").business_name is None
    # ... unless it matches the domain
    assert identify("<title>Acme</title>", url="https://acme.example/offers").business_name == "Acme"


def test_title_only_generic_words_gives_nothing():
    for title in ("Home", "Contact us", "Welcome | Home", "خانه", "تماس با ما | صفحه اصلی"):
        assert identify(f"<title>{title}</title>").business_name is None, title


def test_homepage_heading_is_the_last_resort():
    idn = identify("", "<h1>Acme Plumbing</h1><h1>Second</h1>")
    assert (idn.business_name, idn.name_source) == ("Acme Plumbing", "homepage_heading")
    assert identify("<title>Acme</title>", "<h1>Other Brand</h1>").name_source == "title"  # title ranks higher


@pytest.mark.parametrize("heading", [
    "We fix leaking pipes fast.", "Fast, friendly plumbing for your whole home", "Welcome", "Why choose us?", "",
])
def test_homepage_heading_must_look_like_a_name(heading):
    assert identify("", f"<h1>{heading}</h1>").business_name is None


def test_heading_is_only_used_on_the_homepage():
    assert identify("", "<h1>Acme Plumbing</h1>", url="https://acme.example/contact").business_name is None
    assert page("", "<h1>Acme Plumbing</h1>", "https://acme.example/").classification.category is PageCategory.HOMEPAGE


# ---------------------------------------------------------------- 4. missing business name
def test_missing_business_name():
    idn = identify()
    assert idn.business_name is None and idn.name_source is None
    assert idn.candidates == () and idn.conflict is False
    assert (idn.website, idn.domain) == ("https://acme.example/", "acme.example")  # identity of the site is still known
    assert idn.description is None and idn.description_source is None


def test_empty_page_and_blank_values():
    parsed = parse_html("")
    idn = extract_identity(parsed, source_url="https://acme.example/x")
    assert idn.business_name is None and idn.website == "https://acme.example/"
    assert identify("<title>   </title><meta property='og:site_name' content=' '>").business_name is None


# ---------------------------------------------------------------- 5. conflicting names
def test_conflicting_names_follow_priority_and_are_reported():
    idn = identify(f'<title>Totally Other Co</title><meta property="og:site_name" content="Different Brand">{ld(name="Acme Plumbing")}')
    assert idn.business_name == "Acme Plumbing" and idn.name_source == "json_ld"
    assert idn.conflict is True
    assert [(c.name, c.source) for c in idn.candidates] == [
        ("Acme Plumbing", "json_ld"), ("Different Brand", "og_site_name"), ("Totally Other Co", "title")]


@pytest.mark.parametrize("a,b", [
    ("Acme", "Acme Plumbing Ltd"),          # one contains the other
    ("ACME Plumbing", "acme-plumbing"),     # case and punctuation
    ("Acme  Plumbing", "Acme Plumbing!"),
])
def test_compatible_names_are_not_a_conflict(a, b):
    idn = identify(f'<title>{b}</title>{ld(name=a)}')
    assert idn.business_name == " ".join(a.split()) and len(idn.candidates) == 2 and idn.conflict is False


def test_single_candidate_is_never_a_conflict():
    assert identify("<title>Acme Plumbing</title>").conflict is False


def test_conflict_is_judged_after_arabic_persian_letter_unification():
    same_a, same_b = "كيان پارس", "کیان پارس"  # Arabic ك ي vs Persian ک ی
    idn = identify(f'<meta property="og:site_name" content="{same_a}">{ld(name=same_b)}')
    assert same_a != same_b and len(idn.candidates) == 2 and idn.conflict is False


# ---------------------------------------------------------------- 6. Persian business name
def test_persian_json_ld_name():
    idn = identify(ld(name="شرکت می\u200cخواهم پارس"), url="https://pars.example/", lang="fa")
    assert idn.business_name == "شرکت می\u200cخواهم پارس" and idn.name_source == "json_ld"  # ZWNJ kept


def test_persian_title_fallback_and_generic_persian_segments():
    idn = identify("<title>تماس با ما | شرکت پارس</title>", url="https://pars.example/contact", lang="fa")
    assert (idn.business_name, idn.name_source) == ("شرکت پارس", "title")
    idn = identify("<title>شرکت پارس | خانه</title>", url="https://pars.example/", lang="fa")
    assert idn.business_name == "شرکت پارس"
    idn = identify("<title>خدمات لوله‌کشی | پارس</title>", url="https://pars.example/services", lang="fa")
    assert idn.business_name == "پارس"


def test_persian_open_graph_site_name_and_description():
    idn = identify('<meta property="og:site_name" content="لوله‌کشی پارس"><meta name="description" content="خدمات لوله\u200cکشی در تهران">',
                   url="https://pars.example/", lang="fa")
    assert idn.business_name == "لوله‌کشی پارس" and idn.description == "خدمات لوله\u200cکشی در تهران"


def test_persian_domain_matches_persian_name():
    idn = identify("<title>درباره ما | پارس</title>", url="https://پارس.ir/about", lang="fa")
    assert idn.business_name == "پارس" and idn.domain == "xn--mgbug11a.ir"


def test_persian_homepage_heading():
    idn = identify("", "<h1>گروه صنعتی پارس</h1>", url="https://pars.example/", lang="fa")
    assert (idn.business_name, idn.name_source) == ("گروه صنعتی پارس", "homepage_heading")
    assert identify("", "<h1>ما بهترین خدمات را ارائه می‌دهیم.</h1>", url="https://pars.example/").business_name is None


# ---------------------------------------------------------------- 7. mixed-language business name
def test_mixed_language_name_is_kept_as_written():
    idn = identify(ld(name="Acme آکمی Plumbing"), lang="fa")
    assert idn.business_name == "Acme آکمی Plumbing"


def test_mixed_language_title_with_domain_match():
    idn = identify("<title>تماس با ما | Pars Co پارس</title>", url="https://pars.example/contact")
    assert idn.business_name == "Pars Co پارس"
    idn = identify("<title>Pars Plumbing | شرکت لوله‌کشی پارس</title>", url="https://pars-plumbing.example/")
    assert idn.business_name == "Pars Plumbing"  # matches the domain label "parsplumbing"


def test_english_and_persian_candidates_of_one_business_conflict_honestly():
    idn = identify(f'<meta property="og:site_name" content="پارس">{ld(name="Pars Plumbing")}')
    assert idn.business_name == "Pars Plumbing" and idn.conflict is True  # no transliteration: reported, not guessed


def test_mixed_title_separators_with_persian():
    assert identify("<title>درباره ما — Acme Plumbing</title>", url="https://acme.example/about").business_name == "Acme Plumbing"


# ---------------------------------------------------------------- 8. domain (and website) extraction
@pytest.mark.parametrize("url,website,domain", [
    ("https://acme.example/contact", "https://acme.example/", "acme.example"),
    ("https://www.acme.example/a/b?x=1#f", "https://www.acme.example/", "acme.example"),
    ("HTTP://Shop.Acme.Co.UK/x", "http://shop.acme.co.uk/", "acme.co.uk"),
    ("https://acme.example:8443/x", "https://acme.example:8443/", "acme.example"),
    ("https://پارس.ir/درباره", "https://xn--mgbug11a.ir/", "xn--mgbug11a.ir"),
    ("http://127.0.0.1:8080/p", "http://127.0.0.1:8080/", "127.0.0.1"),
])
def test_website_and_domain_from_source_url(url, website, domain):
    idn = extract_identity(parse_html("<title>x</title>"), source_url=url)
    assert (idn.website, idn.domain) == (website, domain)


def test_explicit_website_wins_over_source_url():
    idn = identify("<title>Acme</title>", url="https://acme.example/contact", website="https://www.acme.example/home")
    assert idn.website == "https://www.acme.example/home" and idn.domain == "acme.example"


@pytest.mark.parametrize("source_url", ["not a url", "", "/relative/path", "mailto:a@b.example", "ftp://acme.example/"])
def test_unusable_source_url_raises(source_url):
    with pytest.raises(LeadDataError):
        extract_identity(parse_html("<title>x</title>"), source_url=source_url)


def test_unusable_explicit_website_raises():
    with pytest.raises(LeadDataError):
        extract_identity(parse_html("<title>x</title>"), source_url="https://acme.example/", website="nope")


def test_domain_helper_functions():
    assert ident.domain_of("https://shop.acme.co.uk/") == "acme.co.uk"
    assert ident.website_origin("https://acme.example/a/b") == "https://acme.example/"
    assert ident.normalize_website("HTTPS://ACME.example/a/") == "https://acme.example/a"


# ---------------------------------------------------------------- description
def test_description_priority():
    full = identify(f'<meta name="description" content="Meta text"><meta property="og:description" content="OG text">{ld(name="Acme", description="Schema text")}')
    assert (full.description, full.description_source) == ("Schema text", "json_ld")
    meta = identify('<meta name="description" content="Meta text"><meta property="og:description" content="OG text">')
    assert (meta.description, meta.description_source) == ("Meta text", "meta_description")
    og = identify('<meta property="og:description" content="  OG text  ">')
    assert (og.description, og.description_source) == ("OG text", "og_description")
    assert identify('<meta name="description" content="   ">').description is None


# ---------------------------------------------------------------- determinism / serialisation / lead integration
def test_identity_is_deterministic_serialisable_and_does_not_touch_the_page():
    parsed = page(f"<title>Contact us | Acme Plumbing</title>{ld(name='Acme Plumbing', description='Plumbers')}", url="https://acme.example/contact")
    before = parsed.to_dict()
    a = extract_identity(parsed, source_url="https://acme.example/contact")
    b = extract_identity(parsed, source_url="https://acme.example/contact")
    assert a == b and parsed.to_dict() == before
    d = a.to_dict()
    assert json.loads(json.dumps(d, ensure_ascii=False)) == d
    assert list(d) == ["business_name", "name_source", "website", "domain", "description", "description_source", "candidates", "conflict"]
    assert d["candidates"] == [{"name": "Acme Plumbing", "source": "json_ld"}, {"name": "Acme Plumbing", "source": "title"}]
    assert isinstance(a, BusinessIdentity)


def test_lead_builder_uses_identity_rules():
    parsed = page("<title>Contact us | Acme Plumbing</title>", url="https://acme.example/contact")
    lead = BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/contact")
    assert lead.business_name == "Acme Plumbing" and lead.domain == "acme.example"
    persian = page("<title>تماس با ما | شرکت پارس</title>", url="https://pars.example/contact", lang="fa")
    lead = BusinessLead.from_parsed_page(persian, source_url="https://pars.example/contact")
    assert lead.business_name == "شرکت پارس" and BusinessLead.from_json(lead.to_json()) == lead


# ---------------------------------------------------------------- crawler untouched: identity from crawled pages, no extra requests
def test_identity_from_crawled_pages_adds_no_requests(local_network):
    home = b'<html><head><title>Acme Plumbing | Plumbers</title><meta property="og:site_name" content="Acme Plumbing"></head><body><a href="/contact">c</a></body></html>'
    contact = b'<html lang="en"><head><title>Contact us | Acme Plumbing</title></head><body><h1>Contact us</h1></body></html>'
    routes = {"/": (200, home, HTML_HEADERS), "/contact": (200, contact, HTML_HEADERS)}
    with server(routes) as (base, state):
        result = crawl(base)
        requests = list(state.requests)
        identities = {p.url.removeprefix(base) or "/": extract_identity(p.parsed, source_url=p.url) for p in result.pages}
        assert list(state.requests) == requests  # extraction made no request
    assert requests == ["/", "/contact"]
    assert identities["/"].business_name == "Acme Plumbing" and identities["/"].name_source == "og_site_name"
    assert identities["/contact"].business_name == "Acme Plumbing" and identities["/contact"].name_source == "title"
    assert {i.domain for i in identities.values()} == {"127.0.0.1"} and identities["/"].website == base + "/"
