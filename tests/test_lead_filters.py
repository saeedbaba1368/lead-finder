"""Phase 10.3: deterministic Lead filtering (pure, offline; no scoring, fuzzy search or enrichment)."""

from __future__ import annotations

import itertools
import socket

import pytest
from sqlalchemy import select

from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.leads import (
    BusinessLead, LeadDataError, LeadFilter, LeadFilterError, filter_leads, filter_stored_leads, leads_to_csv, leads_to_json,
)
from app.models import Lead
from app.repositories import LeadRepository

PHONE = [PhoneNumber("+14155550199", "+1 415 555 0199")]
SOCIAL = [SocialLink("x", "https://x.com/acme")]


def make(domain, **fields) -> BusinessLead:
    return BusinessLead(website=f"https://{domain}/", **fields)


ACME = make("acme.example", business_name="Acme", language="en", page_type=PageCategory.HOMEPAGE, emails=["info@acme.example"],
            phones=PHONE, social_profiles=SOCIAL, address=Address(city="Springfield", country="US"))
BOLT = make("bolt.example", business_name="Bolt", language="en", page_type=PageCategory.CONTACT, emails=["hi@bolt.example"],
            address=Address(city="London", country="UK"))
ARMAN = make("arman.example", business_name="لوله‌کشی آرمان", language="fa", page_type=PageCategory.ABOUT, phones=[PhoneNumber("+982112345678", "۰۲۱-۱۲۳۴۵۶۷۸")],
             social_profiles=[SocialLink("instagram", "https://www.instagram.com/arman")], address=Address(city="تهران", country="ایران"))
BARE = make("bare.example")  # only the required website
MIXED = make("mixed.example", language="mixed", emails=["a@mixed.example"], phones=PHONE, address=Address(city="Tehran", country="Iran"))
ALL = [ACME, BOLT, ARMAN, BARE, MIXED]


def domains(leads) -> list[str]:
    return [lead.domain for lead in leads]


@pytest.fixture
def repo(session):
    return LeadRepository(session)


# ---------------------------------------------------------------- 1. no filters
def test_no_filters_return_all_leads_in_domain_order():
    everything = ["acme.example", "arman.example", "bare.example", "bolt.example", "mixed.example"]
    assert domains(filter_leads(ALL)) == everything
    assert domains(filter_leads(ALL, LeadFilter())) == everything
    assert domains(filter_leads(reversed(ALL), None)) == everything
    assert LeadFilter().is_empty and LeadFilter(language=None, has_email=None).is_empty
    assert filter_leads([]) == []


def test_no_filters_on_the_database(repo):
    for lead in ALL:
        repo.save(lead)
    assert filter_stored_leads(repo) == filter_leads(ALL)


# ---------------------------------------------------------------- 2. one filter
def test_domain_filter_accepts_any_spelling_and_is_exact():
    assert domains(filter_leads(ALL, domain="acme.example")) == ["acme.example"]
    assert domains(filter_leads(ALL, domain="https://WWW.Acme.example/contact")) == ["acme.example"]
    assert filter_leads(ALL, domain="acme") == [] and filter_leads(ALL, domain="acme.example.org") == []  # no partial match
    assert domains(filter_leads(ALL, domain=["acme.example", "bolt.example"])) == ["acme.example", "bolt.example"]


def test_page_type_filter():
    assert domains(filter_leads(ALL, page_type=PageCategory.CONTACT)) == ["bolt.example"]
    assert domains(filter_leads(ALL, page_type="About")) == ["arman.example"]
    assert domains(filter_leads(ALL, page_type=["homepage", PageCategory.ABOUT])) == ["acme.example", "arman.example"]


# ---------------------------------------------------------------- 3. multiple filters
def test_criteria_are_combined_with_and_values_with_or():
    assert domains(filter_leads(ALL, language="en", has_email=True, has_phone=True)) == ["acme.example"]
    assert domains(filter_leads(ALL, language=["en", "fa"], has_social_profile=True)) == ["acme.example", "arman.example"]
    assert filter_leads(ALL, language="fa", country="UK") == []


def test_filters_compose_with_and():
    a, b = LeadFilter(language=["en", "fa"]), LeadFilter(has_email=True, language=["fa", "mixed", "en"])
    both = a & b
    assert both.language == ("en", "fa") and both.has_email is True
    assert filter_leads(ALL, both) == filter_leads(filter_leads(ALL, a), b)
    assert (a & b) == (b & a) and ((a & b) & LeadFilter(city="London")) == (a & (b & LeadFilter(city="London")))
    assert domains(filter_leads(ALL, a, has_phone=True)) == ["acme.example", "arman.example"]  # filter + keyword criteria
    assert a & LeadFilter() == a
    assert isinstance(hash(both), int)


def test_contradictory_filters_match_nothing_without_error():
    assert filter_leads(ALL, LeadFilter(language="en") & LeadFilter(language="fa")) == []
    assert filter_leads(ALL, LeadFilter(has_email=True) & LeadFilter(has_email=False)) == []
    assert (LeadFilter(has_email=True) & LeadFilter(has_email=False)).is_empty is False
    assert filter_leads(ALL, LeadFilter(has_email=True) & LeadFilter(has_email=True)) == filter_leads(ALL, has_email=True)


# ---------------------------------------------------------------- 4/5. has_email, has_phone (+ social)
def test_has_email():
    assert domains(filter_leads(ALL, has_email=True)) == ["acme.example", "bolt.example", "mixed.example"]
    assert domains(filter_leads(ALL, has_email=False)) == ["arman.example", "bare.example"]


def test_has_phone():
    assert domains(filter_leads(ALL, has_phone=True)) == ["acme.example", "arman.example", "mixed.example"]
    assert domains(filter_leads(ALL, has_phone=False)) == ["bare.example", "bolt.example"]


def test_has_social_profile():
    assert domains(filter_leads(ALL, has_social_profile=True)) == ["acme.example", "arman.example"]
    assert domains(filter_leads(ALL, has_social_profile=False)) == ["bare.example", "bolt.example", "mixed.example"]


def test_has_false_and_has_true_partition_the_leads():
    for name in ("has_email", "has_phone", "has_social_profile"):
        yes, no = filter_leads(ALL, **{name: True}), filter_leads(ALL, **{name: False})
        assert sorted(domains(yes) + domains(no)) == sorted(domains(ALL)) and not set(domains(yes)) & set(domains(no))


# ---------------------------------------------------------------- 6. language
def test_language():
    assert domains(filter_leads(ALL, language="en")) == ["acme.example", "bolt.example"]
    assert domains(filter_leads(ALL, language=" FA ")) == ["arman.example"]  # case and spaces
    assert domains(filter_leads(ALL, language="mixed")) == ["mixed.example"]
    assert filter_leads(ALL, language="de") == []
    assert "bare.example" not in domains(filter_leads(ALL, language=["en", "fa", "mixed", "unknown"]))  # no language never matches


# ---------------------------------------------------------------- 7. city / country
def test_city_and_country():
    assert domains(filter_leads(ALL, country="us")) == ["acme.example"]
    assert domains(filter_leads(ALL, city="london")) == ["bolt.example"]
    assert domains(filter_leads(ALL, country="UK", city="London")) == ["bolt.example"]
    assert filter_leads(ALL, country="UK", city="Springfield") == []
    assert domains(filter_leads(ALL, city=["London", "Springfield"])) == ["acme.example", "bolt.example"]
    assert "bare.example" not in domains(filter_leads(ALL, country=["US", "UK", "Iran", "ایران"]))  # no address never matches


def test_matching_is_exact_not_fuzzy():
    assert filter_leads(ALL, city="Teheran") == [] and filter_leads(ALL, city="Tehra") == []
    assert domains(filter_leads(ALL, city="Tehran")) == ["mixed.example"]  # the Latin spelling is not the Persian one
    assert filter_leads(ALL, country="United Kingdom") == []


# ---------------------------------------------------------------- 8. empty result
def test_empty_result_is_an_empty_list_not_an_error(repo):
    assert filter_leads(ALL, language="en", country="Iran") == []
    assert filter_leads([], language="en") == []
    repo.save(ACME)
    assert filter_stored_leads(repo, language="fa") == []
    assert leads_to_json(filter_leads(ALL, language="de")).count('"leads": []') == 1  # an empty selection still exports


# ---------------------------------------------------------------- 9. invalid filter
@pytest.mark.parametrize("criteria, fragment", [
    ({"colour": "red"}, "unknown filter criteria: colour"),
    ({"has_email": "yes"}, "has_email: expected True or False"),
    ({"has_phone": 1}, "has_phone: expected True or False"),
    ({"language": ""}, "language: the value must not be blank"),
    ({"city": "   "}, "city: the value must not be blank"),
    ({"country": 5}, "country: expected text"),
    ({"language": []}, "language: an empty list"),
    ({"page_type": "landing"}, "unknown page type 'landing' (valid: "),
    ({"page_type": 3}, "unknown page type 3"),
    ({"domain": ""}, "domain: expected a domain or URL"),
    ({"domain": "not a domain!"}, "domain: 'not a domain!' is not a usable domain"),
    ({"language": ["en", None]}, "language: expected text, got NoneType"),
])
def test_invalid_filters_fail_clearly(criteria, fragment):
    with pytest.raises(LeadFilterError) as caught:
        filter_leads(ALL, **criteria)
    assert fragment in str(caught.value)
    if "colour" not in criteria:  # an unknown keyword name given straight to the constructor is Python's own TypeError
        with pytest.raises(LeadFilterError):
            LeadFilter(**criteria)
    else:
        with pytest.raises(LeadFilterError, match="unknown filter criteria: colour"):
            LeadFilter.from_dict(criteria)


def test_invalid_filter_errors_are_lead_data_errors_and_other_misuse_is_clear(repo):
    assert issubclass(LeadFilterError, LeadDataError)
    with pytest.raises(LeadFilterError, match="must be a mapping"):
        LeadFilter.from_dict(["language"])  # type: ignore[arg-type]
    with pytest.raises(LeadFilterError, match="must be a LeadFilter"):
        filter_leads(ALL, {"language": "en"})  # type: ignore[arg-type]
    with pytest.raises(LeadFilterError, match="can only filter BusinessLead"):
        filter_leads([ACME, {"website": "https://x.example/"}])  # type: ignore[list-item]
    with pytest.raises(LeadFilterError, match="unknown filter criteria"):
        filter_stored_leads(repo, nonsense=True)
    with pytest.raises(LeadFilterError, match="unknown filter criteria"):
        LeadFilter.from_dict({"never": True})


# ---------------------------------------------------------------- 10. Unicode values
def test_persian_values():
    assert domains(filter_leads(ALL, city="تهران")) == ["arman.example"]
    assert domains(filter_leads(ALL, country="ایران")) == ["arman.example"]
    assert domains(filter_leads(ALL, city="تهران", country="ایران", language="fa")) == ["arman.example"]
    assert domains(filter_leads(ALL, city=["تهران", "Tehran"])) == ["arman.example", "mixed.example"]


def test_arabic_letter_forms_and_unicode_normalisation_match_the_same_text():
    arabic_yeh = "ا\u064aران"  # Arabic yeh (U+064A) instead of Persian yeh (U+06CC)
    assert arabic_yeh != "ایران" and domains(filter_leads(ALL, country=arabic_yeh)) == ["arman.example"]
    fullwidth = "ＵＳ"  # NFKC folds full-width letters
    assert domains(filter_leads(ALL, country=fullwidth)) == ["acme.example"]
    spaced = make("spaced.example", address=Address(city="New   York"))
    assert domains(filter_leads([spaced], city="new york")) == ["spaced.example"]


def test_zwnj_is_significant():
    lead = make("zw.example", address=Address(city="می‌شود"))
    assert filter_leads([lead], city="می‌شود") == [lead]
    assert filter_leads([lead], city="میشود") == []


def test_idn_domain_filter():
    lead = make("münchen.de")
    assert filter_leads([lead], domain="münchen.de") == [lead] and filter_leads([lead], domain="xn--mnchen-3ya.de") == [lead]


# ---------------------------------------------------------------- determinism, no mutation, no network, exports
def test_results_do_not_depend_on_input_order():
    expected = filter_leads(ALL, has_email=True)
    for order in itertools.permutations(ALL):
        assert filter_leads(order, has_email=True) == expected
    assert filter_leads(ALL, language="en") == filter_leads(ALL, language="en")


def test_filtering_does_not_mutate_stored_leads(session, repo):
    for lead in ALL:
        repo.save(lead)
    session.commit()

    def snapshot():
        return [(r.id, r.updated_at, r.emails, r.website, r.language) for r in session.scalars(select(Lead).order_by(Lead.id))]

    before, originals = snapshot(), [lead.to_json() for lead in ALL]
    filter_stored_leads(repo, language="en", has_email=True)
    filter_stored_leads(repo)
    filter_leads(ALL, has_phone=True)
    assert not session.new and not session.dirty and not session.deleted
    assert snapshot() == before and [lead.to_json() for lead in ALL] == originals


def test_filter_stored_leads_matches_the_pure_filter_across_batches(repo, monkeypatch):
    import app.leads.filters as module

    monkeypatch.setattr(module, "_BATCH", 2)
    for lead in ALL:
        repo.save(lead)
    assert filter_stored_leads(repo, has_phone=True) == filter_leads(ALL, has_phone=True)
    assert filter_stored_leads(repo, LeadFilter(language="en"), has_email=True) == filter_leads(ALL, language="en", has_email=True)


def test_no_network_is_used(monkeypatch, repo):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    repo.save(ACME)
    assert domains(filter_stored_leads(repo, domain="acme.example")) == ["acme.example"]


def test_a_filtered_selection_can_be_exported():
    selected = filter_leads(ALL, language="fa")
    assert '"domain": "arman.example"' in leads_to_json(selected) and leads_to_json(selected).count('"domain"') == 1
    assert leads_to_csv(selected).count("\r\n") == 2  # header + one row
    assert leads_to_json(filter_leads(ALL)) == leads_to_json(ALL)


def test_filter_description_is_normalised_and_stable():
    flt = LeadFilter(language=[" FA ", "en", "fa"], domain="WWW.Acme.example", has_email=True, page_type="Contact")
    assert flt.to_dict() == {
        "domain": ["acme.example"], "language": ["en", "fa"], "page_type": ["contact"], "has_email": True,
    }
    assert list(flt.to_dict()) == ["domain", "language", "page_type", "has_email"]  # fixed criterion order
