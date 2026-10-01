"""Phase 8.3: lead data aggregation (pure: no network, no crawler; pages are parsed from strings)."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime

import pytest

from app.crawler import PageCategory, PhoneNumber, SocialLink, parse_html
from app.crawler.structured import Address
from app.leads import BusinessLead, LeadDataError, PageSource, aggregate_leads, aggregate_parsed_pages

T1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 2, 6, 11, 30, tzinfo=UTC)
T3 = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)


def page(head: str = "", body: str = "", url: str = "https://acme.example/", lang: str = "en"):
    return parse_html(f'<html lang="{lang}"><head>{head}</head><body>{body}</body></html>', url)


def ld(**fields) -> str:
    node = {"@context": "https://schema.org", "@type": "LocalBusiness", **fields}
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


def lead(url: str = "https://acme.example/", **fields) -> BusinessLead:
    fields.setdefault("website", "https://acme.example/")
    return BusinessLead(source_url=url, **fields)


# ---------------------------------------------------------------- 1. complete page aggregation
def complete_page():
    head = (
        "<title>Acme Plumbing</title>"
        '<meta name="description" content="Plumbers in Springfield">'
        + ld(
            name="Acme Plumbing", url="https://acme.example/", telephone="+1 415 555 0134",
            email="sales@acme.example", sameAs=["https://www.linkedin.com/company/acme"],
            address={"@type": "PostalAddress", "streetAddress": "1 Main St", "addressLocality": "Springfield", "addressCountry": "US"},
        )
    )
    body = (
        '<h1>Acme Plumbing</h1><p>Email <a href="mailto:info@acme.example">us</a>, call +1 415 555 0199.</p>'
        '<a href="https://twitter.com/acme">X</a>'
    )
    return page(head, body)


def test_complete_page_is_aggregated_into_one_lead():
    result = aggregate_parsed_pages([(complete_page(), "https://acme.example/")])
    assert result.website == "https://acme.example/" and result.domain == "acme.example"
    assert result.business_name == "Acme Plumbing"
    assert result.description == "Plumbers in Springfield"
    assert result.emails == ("info@acme.example", "sales@acme.example")
    assert {p.number for p in result.phones} == {"+14155550134", "+14155550199"}
    assert result.address is not None and result.address.street == "1 Main St" and result.address.city == "Springfield"
    assert {s.platform for s in result.social_profiles} == {"linkedin", "x"}
    assert result.page_type is PageCategory.HOMEPAGE
    assert result.language == "en"
    assert result.source_url == "https://acme.example/"


def test_single_page_aggregation_equals_the_page_lead():
    parsed = complete_page()
    single = BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/", seen_at=T1)
    assert aggregate_parsed_pages([PageSource(parsed, "https://acme.example/", T1)]) == single
    assert aggregate_leads([single]) == single


def test_aggregate_result_is_json_serialisable_and_round_trips():
    result = aggregate_parsed_pages([(complete_page(), "https://acme.example/")])
    assert BusinessLead.from_json(result.to_json()) == result


# ---------------------------------------------------------------- 2. missing fields
def test_page_without_any_data_gives_a_website_only_lead():
    result = aggregate_parsed_pages([(page(), "https://acme.example/")])
    assert result.website == "https://acme.example/"
    assert result.business_name is None and result.description is None and result.address is None
    assert result.emails == () and result.phones == () and result.social_profiles == ()
    assert result.first_seen is None and result.last_seen is None


def test_gaps_are_filled_from_other_pages_and_none_stays_none():
    home = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE, business_name="Acme", language="en")
    contact = lead("https://acme.example/contact", page_type=PageCategory.CONTACT, description="We fix pipes", emails=["a@acme.example"])
    result = aggregate_leads([home, contact])
    assert result.business_name == "Acme" and result.description == "We fix pipes"
    assert result.emails == ("a@acme.example",)
    assert result.address is None and result.first_seen is None and result.phones == ()


def test_a_missing_field_never_overrides_a_present_one():
    full = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE, business_name="Acme", description="D", language="en")
    empty = lead("https://acme.example/about", page_type=PageCategory.ABOUT)
    for pair in ([full, empty], [empty, full]):
        result = aggregate_leads(pair)
        assert result.business_name == "Acme" and result.description == "D" and result.language == "en"


def test_no_leads_or_foreign_values_are_rejected():
    with pytest.raises(LeadDataError):
        aggregate_leads([])
    with pytest.raises(LeadDataError):
        aggregate_leads([{"website": "https://acme.example"}])  # type: ignore[list-item]
    with pytest.raises(LeadDataError):
        aggregate_leads([lead()], website="not a url")


# ---------------------------------------------------------------- 3. duplicate emails
def test_duplicate_emails_across_pages_and_cases_are_removed():
    a = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE, emails=["Info@Acme.example", "sales@acme.example"])
    b = lead("https://acme.example/contact", page_type=PageCategory.CONTACT, emails=["info@acme.example", "INFO@ACME.EXAMPLE"])
    assert aggregate_leads([a, b]).emails == ("info@acme.example", "sales@acme.example")


def test_duplicate_email_in_html_and_structured_data_on_one_page():
    parsed = page(ld(name="Acme", email="Info@acme.example"), '<a href="mailto:info@acme.example">mail</a> info@acme.example')
    result = aggregate_parsed_pages([(parsed, "https://acme.example/")])
    assert result.emails == ("info@acme.example",)


# ---------------------------------------------------------------- 4. duplicate phones
def test_duplicate_phones_are_merged_by_number_and_raw_is_deterministic():
    a = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE, phones=[PhoneNumber("+14155550199", "+1 415 555 0199")])
    b = lead("https://acme.example/contact", phones=[PhoneNumber("+14155550199", "+1 (415) 555-0199"), PhoneNumber("+14155550134", "+1 415 555 0134")])
    expected = (PhoneNumber("+14155550134", "+1 415 555 0134"), PhoneNumber("+14155550199", "+1 (415) 555-0199"))
    assert aggregate_leads([a, b]).phones == expected
    assert aggregate_leads([b, a]).phones == expected


def test_duplicate_phones_in_html_and_structured_data():
    parsed = page(ld(name="Acme", telephone="+1 415 555 0199"), "<p>Call +1 415 555 0199 or tel +1 415 555 0134</p>")
    result = aggregate_parsed_pages([(parsed, "https://acme.example/")])
    assert sorted(p.number for p in result.phones) == ["+14155550134", "+14155550199"]


# ---------------------------------------------------------------- 5. duplicate social profiles
def test_duplicate_social_profiles_are_removed_case_insensitively():
    a = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE,
             social_profiles=[SocialLink("x", "https://x.com/acme"), SocialLink("linkedin", "https://www.linkedin.com/company/acme")])
    b = lead("https://acme.example/about", social_profiles=[SocialLink("x", "https://x.com/Acme"), SocialLink("instagram", "https://www.instagram.com/acme")])
    result = aggregate_leads([a, b])
    assert [s.platform for s in result.social_profiles] == ["instagram", "linkedin", "x"]
    assert aggregate_leads([b, a]) == result
    assert [s.url for s in result.social_profiles if s.platform == "x"] == ["https://x.com/Acme"]  # smallest case variant


def test_duplicate_social_profiles_from_anchors_and_same_as():
    parsed = page(
        ld(name="Acme", sameAs=["https://www.facebook.com/acme", "https://twitter.com/acme"]),
        '<a href="https://www.facebook.com/acme/">fb</a><a href="https://x.com/acme">x</a><a href="https://twitter.com/acme">tw</a>',
    )
    result = aggregate_parsed_pages([(parsed, "https://acme.example/")])
    assert sorted((s.platform, s.url.lower()) for s in result.social_profiles) == [
        ("facebook", "https://www.facebook.com/acme"), ("x", "https://x.com/acme"),
    ]


# ---------------------------------------------------------------- 6. structured data + HTML data
def test_structured_and_html_data_are_combined():
    head = "<title>Acme Plumbing</title>" + ld(name="Acme Plumbing Ltd", telephone="+1 415 555 0134", email="sales@acme.example")
    parsed = page(head, "<p>Mail info@acme.example, phone +1 415 555 0199</p>")
    result = aggregate_parsed_pages([(parsed, "https://acme.example/")])
    assert result.business_name == "Acme Plumbing Ltd"  # JSON-LD beats the title
    assert result.emails == ("info@acme.example", "sales@acme.example")
    assert [p.number for p in result.phones] == ["+14155550134", "+14155550199"]


def test_structured_address_wins_over_html_address_and_fields_are_not_mixed():
    parsed = page(
        ld(name="Acme", address={"@type": "PostalAddress", "streetAddress": "1 Main St", "addressLocality": "Springfield"}),
        "<address>9 Other Road, Shelbyville</address>",
    )
    result = aggregate_parsed_pages([(parsed, "https://acme.example/")])
    assert result.address is not None
    assert result.address.street == "1 Main St" and result.address.city == "Springfield"
    assert "Other Road" not in (result.address.formatted or "")


def test_html_address_is_used_when_there_is_no_structured_one():
    parsed = page("", "<address>9 Other Road, Shelbyville</address>")
    result = aggregate_parsed_pages([(parsed, "https://acme.example/")])
    assert result.address is not None and "Other Road" in (result.address.formatted or "")


def test_description_prefers_structured_data_then_meta():
    both = page(ld(name="Acme", description="From JSON-LD") + '<meta name="description" content="From meta">')
    only_meta = page('<meta name="description" content="From meta">')
    assert aggregate_parsed_pages([(both, "https://acme.example/")]).description == "From JSON-LD"
    assert aggregate_parsed_pages([(only_meta, "https://acme.example/")]).description == "From meta"


# ---------------------------------------------------------------- 7. Persian content
def test_persian_page_is_aggregated():
    head = (
        "<title>خانه | لوله‌کشی آرمان</title>"
        '<meta name="description" content="خدمات لوله‌کشی ساختمان در تهران">'
        + ld(name="لوله‌کشی آرمان", url="https://arman.example/", telephone="۰۲۱-۱۲۳۴۵۶۷۸")
    )
    body = "<h1>لوله‌کشی آرمان</h1><p>ایمیل: info@arman.example — تلفن ۰۹۱۲۱۲۳۴۵۶۷</p>"
    parsed = page(head, body, url="https://arman.example/", lang="fa")
    result = aggregate_parsed_pages([(parsed, "https://arman.example/")])
    assert result.business_name == "لوله‌کشی آرمان"  # ZWNJ is kept
    assert result.description == "خدمات لوله‌کشی ساختمان در تهران"
    assert result.language == "fa"
    assert result.emails == ("info@arman.example",)
    assert all(p.number.isascii() for p in result.phones) and result.phones  # Persian digits became ASCII
    assert BusinessLead.from_json(result.to_json()) == result
    assert "لوله‌کشی آرمان" in result.to_json()  # not escaped


def test_persian_and_english_pages_of_one_site_vote_for_the_language():
    fa = lead("https://arman.example/", website="https://arman.example/", page_type=PageCategory.HOMEPAGE, language="fa", business_name="آرمان")
    fa2 = lead("https://arman.example/about", website="https://arman.example/", page_type=PageCategory.ABOUT, language="fa")
    en = lead("https://arman.example/en/contact", website="https://arman.example/", page_type=PageCategory.CONTACT, language="en")
    result = aggregate_leads([en, fa2, fa])
    assert result.language == "fa" and result.business_name == "آرمان"


# ---------------------------------------------------------------- 8. multiple pages of one site
def site_pages():
    home = page("<title>Acme Plumbing</title>" + '<meta name="description" content="Plumbers in Springfield">',
                "<h1>Acme Plumbing</h1>", url="https://acme.example/")
    contact = page("<title>Contact us | Acme Plumbing</title>",
                   "<p>Email info@acme.example or sales@acme.example. Call +1 415 555 0199</p>"
                   '<a href="https://www.facebook.com/acme">fb</a>', url="https://acme.example/contact")
    about = page("<title>About us | Acme Plumbing</title>" + ld(name="Acme Plumbing", telephone="+1 415 555 0199", email="INFO@acme.example",
                 address={"@type": "PostalAddress", "streetAddress": "1 Main St", "addressLocality": "Springfield"}),
                 '<a href="https://www.facebook.com/acme/">fb</a><a href="https://www.instagram.com/acme">ig</a>',
                 url="https://acme.example/about")
    return [
        PageSource(contact, "https://acme.example/contact", T2),
        PageSource(home, "https://acme.example/", T1),
        PageSource(about, "https://acme.example/about", T3),
    ]


def test_pages_of_one_site_become_one_lead():
    result = aggregate_parsed_pages(site_pages())
    assert result.website == "https://acme.example/" and result.domain == "acme.example"
    assert result.business_name == "Acme Plumbing"
    assert result.description == "Plumbers in Springfield"
    assert result.emails == ("info@acme.example", "sales@acme.example")
    assert [p.number for p in result.phones] == ["+14155550199"]
    assert [s.platform for s in result.social_profiles] == ["facebook", "instagram"]
    assert result.address is not None and result.address.street == "1 Main St"
    assert result.page_type is PageCategory.HOMEPAGE and result.source_url == "https://acme.example/"
    assert result.first_seen == T1 and result.last_seen == T3


def test_aggregation_does_not_depend_on_input_order():
    pages = site_pages()
    expected = aggregate_parsed_pages(pages).to_json()
    for order in itertools.permutations(pages):
        assert aggregate_parsed_pages(list(order)).to_json() == expected


def test_aggregation_is_repeatable_and_does_not_change_its_input():
    pages = site_pages()
    leads = [BusinessLead.from_parsed_page(p.parsed, source_url=p.source_url, seen_at=p.seen_at) for p in pages]
    snapshot = [x.to_json() for x in leads]
    first, second = aggregate_leads(leads), aggregate_leads(iter(leads))
    assert first == second and first.to_json() == second.to_json()
    assert [x.to_json() for x in leads] == snapshot


def test_primary_page_is_the_homepage_else_the_smallest_source_url():
    about = lead("https://acme.example/about", page_type=PageCategory.ABOUT, business_name="From About")
    contact = lead("https://acme.example/contact", page_type=PageCategory.CONTACT, business_name="From Contact")
    result = aggregate_leads([contact, about])
    assert result.source_url == "https://acme.example/about" and result.page_type is PageCategory.ABOUT
    assert result.business_name == "From About"
    home = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE)
    result = aggregate_leads([contact, about, home])
    assert result.source_url == "https://acme.example/" and result.page_type is PageCategory.HOMEPAGE
    assert result.business_name == "From About"  # the homepage has none: the next page fills the gap


def test_www_and_plain_host_of_one_domain_are_one_site():
    a = lead("https://www.acme.example/", website="https://www.acme.example/", page_type=PageCategory.HOMEPAGE, emails=["a@acme.example"])
    b = lead("https://acme.example/contact", website="https://acme.example/", emails=["b@acme.example"])
    result = aggregate_leads([b, a])
    assert result.website == "https://www.acme.example/" and result.domain == "acme.example"
    assert result.emails == ("a@acme.example", "b@acme.example")


def test_explicit_website_is_used_for_the_result():
    pages = site_pages()
    assert aggregate_parsed_pages(pages, website="https://acme.example").website == "https://acme.example/"


def test_leads_of_different_websites_are_not_merged():
    with pytest.raises(LeadDataError, match="different websites"):
        aggregate_leads([lead(), BusinessLead(website="https://other.example/", source_url="https://other.example/")])
    with pytest.raises(LeadDataError):
        aggregate_leads([lead()], website="https://other.example/")


def test_language_vote_ignores_unknown_and_ties_follow_page_order():
    home = lead("https://acme.example/", page_type=PageCategory.HOMEPAGE, language="unknown")
    a = lead("https://acme.example/a", language="de")
    b = lead("https://acme.example/b", language="en")
    c = lead("https://acme.example/c", language="en")
    assert aggregate_leads([home, a, b, c]).language == "en"
    assert aggregate_leads([home, a]).language == "de"
    assert aggregate_leads([home]).language == "unknown"
    assert aggregate_leads([lead("https://acme.example/z")]).language is None


def test_seen_range_covers_all_pages():
    a = lead("https://acme.example/a", first_seen=T2, last_seen=T2)
    b = lead("https://acme.example/b", first_seen=T1, last_seen=T3)
    c = lead("https://acme.example/c")
    result = aggregate_leads([a, b, c])
    assert result.first_seen == T1 and result.last_seen == T3


def test_address_is_taken_whole_from_one_page():
    a = lead("https://acme.example/a", address=Address(street="1 Main St"))
    b = lead("https://acme.example/b", address=Address(city="Springfield", country="US"))
    result = aggregate_leads([b, a])
    assert result.address == Address(street="1 Main St")
