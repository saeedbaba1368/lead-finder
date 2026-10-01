"""Phase 9.4: multi-page lead aggregation (combined lead, conflicts, sources); no fuzzy matching, no enrichment."""

from __future__ import annotations

import itertools
import json
import logging
from datetime import UTC, datetime

import pytest

from app.crawler import PageCategory, PhoneNumber, SocialLink, parse_html
from app.crawler.structured import Address
from app.leads import (
    BusinessLead, LeadDataError, PageSource, aggregate_leads, aggregate_pages, aggregate_parsed_pages, lead_conflicts,
)
from app.repositories import LeadRepository

T1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 2, 6, 11, 30, tzinfo=UTC)
T3 = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
HOME, CONTACT, ABOUT = "https://acme.example/", "https://www.acme.example/contact", "https://acme.example/about"
ADDRESS = Address(street="1 Main St", city="Springfield", country="US", formatted="1 Main St, Springfield, US")


def lead(url, **fields) -> BusinessLead:
    return BusinessLead(website="https://acme.example/", source_url=url, **fields)


def homepage():
    return lead(HOME, business_name="Acme Plumbing", emails=["info@acme.example"], page_type=PageCategory.HOMEPAGE,
                language="en", first_seen=T1, last_seen=T1)


def contact():
    return lead(CONTACT, phones=[PhoneNumber("+14155550199", "+1 415 555 0199")], address=ADDRESS,
                page_type=PageCategory.CONTACT, first_seen=T2, last_seen=T2)


def about():
    return lead(ABOUT, description="Plumbers in Springfield", page_type=PageCategory.ABOUT, first_seen=T3, last_seen=T3)


# ---------------------------------------------------------------- the three-page example
def test_pages_with_complementary_information_are_combined():
    result = aggregate_pages([about(), contact(), homepage()])
    combined = result.lead
    assert combined.business_name == "Acme Plumbing" and combined.emails == ("info@acme.example",)
    assert [p.number for p in combined.phones] == ["+14155550199"] and combined.address == ADDRESS
    assert combined.description == "Plumbers in Springfield"
    assert (combined.page_type, combined.source_url) == (PageCategory.HOMEPAGE, HOME)
    assert (combined.first_seen, combined.last_seen) == (T1, T3)
    assert result.conflicts == ()
    assert result.lead == aggregate_leads([homepage(), contact(), about()])  # same lead as the one-shot aggregation


def test_every_input_order_gives_the_identical_result():
    pages = [homepage(), contact(), about(), lead("https://acme.example/team", emails=["z@acme.example"])]
    results = {aggregate_pages(p).to_json() for p in itertools.permutations(pages)}
    assert len(results) == 1


# ---------------------------------------------------------------- no duplicates
def test_no_duplicate_values():
    a = lead(HOME, emails=["Info@Acme.example"], phones=[PhoneNumber("+14155550199", "B")],
             social_profiles=[SocialLink("x", "https://x.com/Acme")], page_type=PageCategory.HOMEPAGE)
    b = lead(CONTACT, emails=["info@acme.example", "sales@acme.example"], phones=[PhoneNumber("+14155550199", "A")],
             social_profiles=[SocialLink("x", "https://x.com/acme")])
    combined = aggregate_pages([a, b, a]).lead
    assert combined.emails == ("info@acme.example", "sales@acme.example")
    assert [(p.number, p.raw) for p in combined.phones] == [("+14155550199", "A")]
    assert len(combined.social_profiles) == 1


def test_the_same_page_twice_is_the_same_lead():
    assert aggregate_pages([homepage(), homepage()]).lead == homepage()


# ---------------------------------------------------------------- conflicts never overwrite
def test_conflicting_name_description_and_address_keep_the_existing_value_and_are_reported():
    other_address = Address(street="9 Side St", city="Elsewhere")
    first = lead(HOME, business_name="Acme Plumbing", description="Plumbers", address=ADDRESS, page_type=PageCategory.HOMEPAGE)
    second = lead(ABOUT, business_name="Acme Heating", description="Heaters", address=other_address)
    result = aggregate_pages([second, first])
    assert (result.lead.business_name, result.lead.description, result.lead.address) == ("Acme Plumbing", "Plumbers", ADDRESS)
    by_field = {c.field: c for c in result.conflicts}
    assert set(by_field) == {"business_name", "description", "address"}
    name = by_field["business_name"]
    assert (name.kept, name.rejected, name.kept_source, name.rejected_source) == ("Acme Plumbing", "Acme Heating", HOME, ABOUT)
    assert by_field["address"].rejected == other_address.to_dict()
    assert [c.field for c in result.conflicts] == ["business_name", "description", "address"]  # fixed order


def test_equal_values_and_missing_values_are_not_conflicts():
    result = aggregate_pages([homepage(), lead(ABOUT, business_name="Acme Plumbing"), contact()])
    assert result.conflicts == ()


def test_similar_names_are_different_values_no_fuzzy_matching():
    result = aggregate_pages([lead(HOME, business_name="Acme Plumbing", page_type=PageCategory.HOMEPAGE),
                              lead(ABOUT, business_name="ACME plumbing")])
    assert result.lead.business_name == "Acme Plumbing"
    assert [c.rejected for c in result.conflicts] == ["ACME plumbing"]


def test_a_rejected_value_is_reported_once():
    pages = [lead(HOME, business_name="A Co", page_type=PageCategory.HOMEPAGE), lead(ABOUT, business_name="B Co"),
             lead(CONTACT, business_name="B Co")]
    conflicts = aggregate_pages(pages).conflicts
    assert [(c.rejected, c.rejected_source) for c in conflicts] == [("B Co", ABOUT)]  # first source in page order


# ---------------------------------------------------------------- sources
def test_field_and_value_sources_are_preserved():
    result = aggregate_pages([homepage(), contact(), about(), lead(ABOUT, emails=["info@acme.example"])])
    assert result.field_sources == {"business_name": HOME, "description": ABOUT, "address": CONTACT}
    assert result.value_sources["emails:info@acme.example"] == (HOME, ABOUT)
    assert result.value_sources["phones:+14155550199"] == (CONTACT,)


def test_pages_without_a_source_url_contribute_no_source():
    page = BusinessLead(website="https://acme.example/", emails=["a@acme.example"])
    result = aggregate_pages([page, homepage()])
    assert "emails:a@acme.example" not in result.value_sources
    assert result.lead.emails == ("a@acme.example", "info@acme.example")


def test_result_serialises_deterministically():
    result = aggregate_pages([homepage(), contact(), about()])
    data = json.loads(result.to_json())
    assert set(data) == {"lead", "conflicts", "field_sources", "value_sources"}
    assert result.to_json() == aggregate_pages([about(), homepage(), contact()]).to_json()


# ---------------------------------------------------------------- website scope
def test_www_and_bare_domain_pages_are_one_website():
    a = BusinessLead(website="https://www.acme.example/", source_url=HOME, emails=["a@acme.example"], page_type=PageCategory.HOMEPAGE)
    b = BusinessLead(website="http://acme.example/", source_url=CONTACT, emails=["b@acme.example"])
    assert aggregate_pages([a, b]).lead.emails == ("a@acme.example", "b@acme.example")


def test_different_websites_are_never_combined():
    with pytest.raises(LeadDataError):
        aggregate_pages([homepage(), BusinessLead(website="https://acme-plumbing.example/", emails=["x@acme-plumbing.example"])])
    with pytest.raises(LeadDataError):
        aggregate_pages([])
    with pytest.raises(LeadDataError):
        aggregate_pages([homepage(), "nope"])  # type: ignore[list-item]


# ---------------------------------------------------------------- from real parsed pages
def parsed(body, head="", url=HOME):
    return parse_html(f'<html lang="en"><head>{head}</head><body>{body}</body></html>', url)


def test_real_parsed_pages_combine_without_losing_data():
    ld = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness",'
          '"address":{"@type":"PostalAddress","streetAddress":"1 Main St","addressLocality":"Springfield"}}</script>')
    pages = [
        PageSource(parsed('<p><a href="mailto:info@acme.example">mail</a></p>', "<title>Acme Plumbing</title>"), HOME),
        PageSource(parsed("<p>Call +1 415 555 0199</p>", "<title>Contact | Acme Plumbing</title>" + ld, CONTACT), CONTACT),
        PageSource(parsed("<p>About us</p>", '<title>About | Acme Plumbing</title><meta name="description" content="Plumbers in Springfield">', ABOUT), ABOUT),
    ]
    combined = aggregate_parsed_pages(pages)
    assert combined.business_name == "Acme Plumbing" and combined.emails == ("info@acme.example",)
    assert [p.number for p in combined.phones] == ["+14155550199"]
    assert combined.address is not None and combined.address.street == "1 Main St"
    assert combined.description == "Plumbers in Springfield"


# ---------------------------------------------------------------- persistence: stored values are kept, conflicts are logged
def test_repository_merge_keeps_stored_values_and_logs_the_conflict(session, caplog):
    repo = LeadRepository(session)
    repo.merge(homepage())
    with caplog.at_level(logging.WARNING):
        repo.merge(lead(ABOUT, business_name="Acme Heating", description="Plumbers in Springfield"))
    stored = repo.load("acme.example")
    assert stored.business_name == "Acme Plumbing" and stored.description == "Plumbers in Springfield"
    assert any(r.message == "lead_conflict" and r.field == "business_name" for r in caplog.records)
    assert [c.field for c in lead_conflicts([stored, lead(ABOUT, business_name="Acme Heating")])] == ["business_name"]


def test_multi_page_merge_into_the_database_matches_the_pure_aggregation(session):
    repo = LeadRepository(session)
    for page in (contact(), about(), homepage(), contact()):
        repo.merge(page)
    stored = repo.load("acme.example")
    expected = aggregate_pages([homepage(), contact(), about()]).lead
    for name in ("business_name", "description", "address", "emails", "phones"):
        assert getattr(stored, name) == getattr(expected, name)
    assert repo.count() == 1
