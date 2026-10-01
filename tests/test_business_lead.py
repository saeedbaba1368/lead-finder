"""Phase 8.1: business lead data model (pure, no network, no database)."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.crawler import PageCategory, PhoneNumber, SocialLink, parse_html
from app.crawler.structured import Address
from app.leads import BusinessLead, LeadDataError

T1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 2, 6, 11, 30, tzinfo=UTC)


def complete() -> BusinessLead:
    return BusinessLead(
        website="https://www.acme.example/",
        business_name="Acme Plumbing",
        description="Plumbers in Springfield",
        emails=["Info@Acme.example", "sales@acme.example", "info@acme.example"],
        phones=[PhoneNumber("+14155550199", "+1 415 555 0199"), PhoneNumber("+14155550134", "+1 415 555 0134")],
        address=Address(street="1 Main St", city="Springfield", country="US", formatted="1 Main St, Springfield, US"),
        social_profiles=[SocialLink("x", "https://x.com/acme"), SocialLink("linkedin", "https://www.linkedin.com/company/acme")],
        page_type=PageCategory.CONTACT,
        language="en",
        source_url="https://www.acme.example/contact",
        first_seen=T1,
        last_seen=T2,
    )


# ---------------------------------------------------------------- 1. minimal lead
def test_minimal_lead_needs_only_a_website():
    lead = BusinessLead(website="https://acme.example")
    assert lead.website == "https://acme.example/"
    assert lead.domain == "acme.example"
    assert lead.business_name is None and lead.description is None and lead.address is None
    assert lead.page_type is None and lead.language is None and lead.source_url is None
    assert lead.first_seen is None and lead.last_seen is None
    assert lead.emails == () and lead.phones == () and lead.social_profiles == ()


def test_website_is_required_and_must_be_http_url():
    with pytest.raises(TypeError):
        BusinessLead()  # type: ignore[call-arg]
    for bad in ("", "   ", "acme.example", "ftp://acme.example", "mailto:a@b.example", "http://", "http://u:p@acme.example"):
        with pytest.raises(LeadDataError):
            BusinessLead(website=bad)
    with pytest.raises(LeadDataError):
        BusinessLead(website=None)  # type: ignore[arg-type]
    with pytest.raises(LeadDataError):
        BusinessLead(website=42)  # type: ignore[arg-type]


def test_lead_is_immutable():
    lead = BusinessLead(website="https://acme.example")
    with pytest.raises(FrozenInstanceError):
        lead.business_name = "x"  # type: ignore[misc]
    assert replace(lead, business_name="  Acme  ").business_name == "Acme"  # replace() re-validates


# ---------------------------------------------------------------- 2. complete lead
def test_complete_lead_is_normalised_and_deterministic():
    lead = complete()
    assert lead.website == "https://www.acme.example/"
    assert lead.domain == "acme.example"  # registrable domain, www dropped
    assert lead.emails == ("info@acme.example", "sales@acme.example")  # lower-case, unique, sorted
    assert [p.number for p in lead.phones] == ["+14155550134", "+14155550199"]  # sorted by number
    assert [s.platform for s in lead.social_profiles] == ["linkedin", "x"]  # sorted by platform
    assert lead.address.city == "Springfield" and lead.page_type is PageCategory.CONTACT
    assert lead.language == "en" and lead.source_url.endswith("/contact")
    assert (lead.first_seen, lead.last_seen) == (T1, T2)
    reordered = replace(lead, emails=list(reversed(lead.emails)), phones=list(reversed(lead.phones)))
    assert reordered == lead and reordered.to_json() == lead.to_json()


def test_domain_is_derived_and_checked():
    assert BusinessLead(website="https://shop.acme.co.uk/x").domain == "acme.co.uk"
    assert BusinessLead(website="https://acme.example", domain="ACME.example").domain == "acme.example"
    with pytest.raises(LeadDataError, match="does not match"):
        BusinessLead(website="https://acme.example", domain="other.example")


def test_non_default_port_is_accepted():
    lead = BusinessLead(website="http://127.0.0.1:8080/")
    assert lead.domain == "127.0.0.1" and ":8080" in lead.website


def test_timestamps_are_utc_and_validated():
    tehran = timezone(timedelta(hours=3, minutes=30))
    lead = BusinessLead(website="https://acme.example", first_seen=datetime(2026, 1, 1, 12, 0, tzinfo=tehran))
    assert lead.first_seen == datetime(2026, 1, 1, 8, 30, tzinfo=UTC) and lead.first_seen.utcoffset() == timedelta(0)
    with pytest.raises(LeadDataError, match="timezone-aware"):
        BusinessLead(website="https://acme.example", first_seen=datetime(2026, 1, 1))
    with pytest.raises(LeadDataError, match="earlier"):
        BusinessLead(website="https://acme.example", first_seen=T2, last_seen=T1)
    with pytest.raises(LeadDataError):
        BusinessLead(website="https://acme.example", last_seen="2026-01-01")  # type: ignore[arg-type]
    assert BusinessLead(website="https://acme.example", first_seen=T1, last_seen=T1).last_seen == T1


@pytest.mark.parametrize("kwargs", [
    {"emails": ["not-an-email"]}, {"emails": ["logo@2x.png"]}, {"emails": [5]}, {"emails": "a@b.example"},
    {"phones": ["+1415"]}, {"phones": [PhoneNumber("abc", "abc")]},
    {"social_profiles": ["https://x.com/a"]}, {"address": "1 Main St"}, {"page_type": "nonsense"}, {"page_type": 3},
    {"business_name": 5}, {"language": 5},
])
def test_invalid_values_raise_lead_data_error(kwargs):
    with pytest.raises(LeadDataError):
        BusinessLead(website="https://acme.example", **kwargs)


# ---------------------------------------------------------------- 3. missing / blank optional fields
def test_blank_optional_values_become_none():
    lead = BusinessLead(
        website="https://acme.example", business_name="   ", description="\n ", language=" ", source_url=" ",
        emails=["", "  "], address=Address(street=" ", city=None), page_type=None,
    )
    assert lead.business_name is None and lead.description is None and lead.language is None
    assert lead.source_url is None and lead.emails == () and lead.address is None


def test_text_normalisation():
    lead = BusinessLead(website="https://acme.example", business_name="  Acme \n  Plumbing\t", description="  Line 1\nLine 2  ", language=" FA ")
    assert lead.business_name == "Acme Plumbing"
    assert lead.description == "Line 1\nLine 2"
    assert lead.language == "fa"
    assert BusinessLead(website="https://acme.example", page_type=" Contact ").page_type is PageCategory.CONTACT


# ---------------------------------------------------------------- 4. serialization
def test_to_dict_has_fixed_json_ready_keys():
    d = complete().to_dict()
    assert list(d) == [
        "business_name", "website", "domain", "description", "emails", "phones", "address", "social_profiles",
        "page_type", "language", "source_url", "first_seen", "last_seen",
    ]
    assert d["emails"] == ["info@acme.example", "sales@acme.example"]
    assert d["phones"][0] == {"number": "+14155550134", "raw": "+1 415 555 0134"}
    assert d["address"]["street"] == "1 Main St"
    assert d["social_profiles"][0] == {"platform": "linkedin", "url": "https://www.linkedin.com/company/acme"}
    assert d["page_type"] == "contact" and d["first_seen"] == "2026-01-05T10:00:00+00:00"
    assert json.loads(json.dumps(d)) == d  # nothing but JSON types


def test_minimal_lead_serialises_with_nones_and_empty_lists():
    d = BusinessLead(website="https://acme.example").to_dict()
    assert d["emails"] == [] and d["phones"] == [] and d["social_profiles"] == []
    assert d["address"] is None and d["page_type"] is None and d["first_seen"] is None and d["business_name"] is None


def test_to_json_is_deterministic():
    assert complete().to_json() == complete().to_json()
    assert list(json.loads(complete().to_json())) == sorted(json.loads(complete().to_json()))


# ---------------------------------------------------------------- 5. deserialization
@pytest.mark.parametrize("make", [complete, lambda: BusinessLead(website="https://acme.example")])
def test_round_trip_dict_and_json(make):
    lead = make()
    assert BusinessLead.from_dict(lead.to_dict()) == lead
    assert BusinessLead.from_json(lead.to_json()) == lead
    assert BusinessLead.from_dict(json.loads(json.dumps(lead.to_dict()))) == lead


def test_from_dict_accepts_missing_optional_keys_and_nulls():
    lead = BusinessLead.from_dict({"website": "https://acme.example", "emails": None, "phones": None, "address": None, "first_seen": None})
    assert lead == BusinessLead(website="https://acme.example")


def test_from_dict_accepts_z_suffix_and_offsets():
    lead = BusinessLead.from_dict({"website": "https://acme.example", "first_seen": "2026-01-05T10:00:00Z", "last_seen": "2026-01-05T13:30:00+03:30"})
    assert lead.first_seen == lead.last_seen == T1


@pytest.mark.parametrize("data", [
    {},                                                          # website missing
    {"website": "https://acme.example", "bogus": 1},             # unknown key
    {"website": "https://acme.example", "emails": "a@b.example"},
    {"website": "https://acme.example", "emails": ["nope"]},
    {"website": "https://acme.example", "phones": [{"number": "+14155550199", "extra": 1}]},
    {"website": "https://acme.example", "phones": ["+14155550199"]},
    {"website": "https://acme.example", "phones": [{"raw": "x"}]},
    {"website": "https://acme.example", "social_profiles": [{"platform": "x"}]},
    {"website": "https://acme.example", "address": {"street": 5}},
    {"website": "https://acme.example", "address": {"nope": "x"}},
    {"website": "https://acme.example", "page_type": "nonsense"},
    {"website": "https://acme.example", "first_seen": "yesterday"},
    {"website": "https://acme.example", "first_seen": 5},
    {"website": "https://acme.example", "first_seen": "2026-01-05T10:00:00"},  # naive
    {"website": "https://acme.example", "domain": "other.example"},
    {"website": 5},
])
def test_from_dict_rejects_invalid_data(data):
    with pytest.raises(LeadDataError):
        BusinessLead.from_dict(data)


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", "5"])
def test_from_json_rejects_invalid_text(text):
    with pytest.raises(LeadDataError):
        BusinessLead.from_json(text)


def test_lead_data_error_is_a_value_error():
    assert issubclass(LeadDataError, ValueError)


# ---------------------------------------------------------------- 6. Unicode / Persian
def persian() -> BusinessLead:
    return BusinessLead(
        website="https://پارس.ir/",
        business_name="شرکت می\u200cخواهم  پارس",
        description="خدمات لوله\u200cکشی در تهران — پاسخگویی ۲۴ ساعته",
        emails=["info@pars.ir"],
        phones=[PhoneNumber("02112345678", "۰۲۱ ۱۲۳۴ ۵۶۷۸")],
        address=Address(street="خیابان ولیعصر", city="تهران", country="ایران", formatted="تهران، خیابان ولیعصر"),
        social_profiles=[SocialLink("instagram", "https://instagram.com/pars_co")],
        page_type=PageCategory.ABOUT,
        language="fa",
        source_url="https://پارس.ir/%D8%AF%D8%B1%D8%A8%D8%A7%D8%B1%D9%87-%D9%85%D8%A7",
        first_seen=T1,
        last_seen=T2,
    )


def test_persian_business_data_is_preserved():
    lead = persian()
    assert lead.business_name == "شرکت می\u200cخواهم پارس"  # whitespace collapsed, ZWNJ kept
    assert "\u200c" in lead.description and "۲۴" in lead.description
    assert lead.phones[0].raw == "۰۲۱ ۱۲۳۴ ۵۶۷۸" and lead.phones[0].number == "02112345678"
    assert lead.address.city == "تهران" and lead.language == "fa"
    assert lead.domain == "xn--mgbug11a.ir"  # IDN host in punycode, like the rest of the URL engine


def test_persian_round_trip_keeps_text_readable_in_json():
    lead = persian()
    text = lead.to_json()
    assert "شرکت" in text and "\\u0634" not in text  # ensure_ascii is off
    assert BusinessLead.from_json(text) == lead
    assert BusinessLead.from_dict(json.loads(json.dumps(lead.to_dict()))) == lead


def test_mixed_script_names_and_emoji_survive():
    lead = BusinessLead(website="https://acme.example", business_name="Café ☕ پارس — Ünïcode")
    assert BusinessLead.from_json(lead.to_json()).business_name == "Café ☕ پارس — Ünïcode"


# ---------------------------------------------------------------- 7. empty contact information
def test_empty_contact_collections():
    for empty in ((), [], None):
        lead = BusinessLead(website="https://acme.example", emails=empty, phones=empty, social_profiles=empty)
        assert lead.emails == () and lead.phones == () and lead.social_profiles == ()
    lead = BusinessLead(website="https://acme.example", emails=iter([]), phones=(p for p in []))
    assert lead.emails == () and lead.phones == ()


def test_empty_contact_round_trips():
    lead = BusinessLead(website="https://acme.example", emails=[], phones=[], social_profiles=[], address=None)
    data = lead.to_dict()
    assert data["emails"] == [] and data["phones"] == [] and data["social_profiles"] == [] and data["address"] is None
    assert BusinessLead.from_dict(data) == lead


def test_duplicate_contact_values_collapse():
    lead = BusinessLead(
        website="https://acme.example", emails=["a@acme.example", "A@ACME.EXAMPLE"],
        phones=[PhoneNumber("+14155550199", "a"), PhoneNumber("+14155550199", "b")],
        social_profiles=[SocialLink("x", "https://x.com/Acme"), SocialLink("x", "https://x.com/acme")],
    )
    assert lead.emails == ("a@acme.example",) and len(lead.phones) == 1 and len(lead.social_profiles) == 1
    assert lead.phones[0].raw == "a"  # first seen wins


# ---------------------------------------------------------------- from a parsed page (reuse, no new extraction)
PAGE = """<html lang="en"><head><title>Contact us | Acme Plumbing</title>
<meta name="description" content="Get in touch with Acme Plumbing">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"Acme Plumbing",
"description":"Plumbers in Springfield","telephone":"+1 415 555 0134","email":"office@acme.example",
"sameAs":["https://www.linkedin.com/company/acme-plumbing"],
"address":{"@type":"PostalAddress","streetAddress":"1 Main St","addressLocality":"Springfield","addressCountry":"US"}}</script>
</head><body><h1>Contact us</h1><p>Write to <a href="mailto:hello@acme.example">hello@acme.example</a> or call +1 415 555 0199.</p>
<a href="https://twitter.com/acmeplumbing">X</a></body></html>"""


def test_lead_from_parsed_page_reuses_extracted_values():
    parsed = parse_html(PAGE, "https://acme.example/contact")
    lead = BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/contact", seen_at=T1)
    assert lead.business_name == "Acme Plumbing" and lead.description == "Plumbers in Springfield"
    assert lead.website == "https://acme.example/"
    assert lead.domain == "acme.example"
    assert lead.emails == ("hello@acme.example", "office@acme.example")
    assert [p.number for p in lead.phones] == ["+14155550134", "+14155550199"]
    assert [(s.platform, s.url) for s in lead.social_profiles] == [
        ("linkedin", "https://www.linkedin.com/company/acme-plumbing"), ("x", "https://x.com/acmeplumbing")]
    assert (lead.address.street, lead.address.city) == ("1 Main St", "Springfield")
    assert lead.page_type is PageCategory.CONTACT and lead.language == "en"
    assert lead.source_url == "https://acme.example/contact" and lead.first_seen == lead.last_seen == T1
    assert BusinessLead.from_json(lead.to_json()) == lead


def test_lead_from_bare_page_has_no_guessed_data():
    parsed = parse_html("<html><head><title>Our latest offers</title></head><body><p>hi</p></body></html>")
    lead = BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/x/y?q=1", website="https://acme.example/")
    assert lead.business_name is None  # the title of an inner page names the page, not the business (Phase 8.2 rules)
    assert lead.description is None and lead.address is None
    assert lead.emails == () and lead.phones == () and lead.social_profiles == ()
    assert lead.first_seen is None and lead.last_seen is None  # no hidden clock
    assert BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/x/y").website == "https://acme.example/"


def test_lead_from_parsed_page_is_deterministic_and_does_not_mutate_the_page():
    parsed = parse_html(PAGE, "https://acme.example/contact")
    before = parsed.to_dict()
    a = BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/contact", seen_at=T1)
    b = BusinessLead.from_parsed_page(parsed, source_url="https://acme.example/contact", seen_at=T1)
    assert a == b and a.to_json() == b.to_json() and parsed.to_dict() == before


def test_lead_from_parsed_page_with_bad_source_url():
    parsed = parse_html("<title>x</title>")
    with pytest.raises(LeadDataError):
        BusinessLead.from_parsed_page(parsed, source_url="not a url")


def test_lead_from_persian_parsed_page():
    html = '<html lang="fa"><head><title>درباره ما | پارس</title></head><body><h1>درباره ما</h1><p>تلفن: ۰۲۱-۱۲۳۴۵۶۷۸ ایمیل info@pars.ir</p></body></html>'
    parsed = parse_html(html, "https://pars.ir/about")
    lead = BusinessLead.from_parsed_page(parsed, source_url="https://pars.ir/about", seen_at=T2)
    assert lead.language == "fa" and lead.emails == ("info@pars.ir",) and lead.page_type is PageCategory.ABOUT
    assert BusinessLead.from_json(lead.to_json()) == lead
