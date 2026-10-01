"""Phase 9.3: exact Lead de-duplication (pure `merge_leads` / `deduplicate_leads` and `LeadRepository.merge`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.db import migrate
from app.leads import BusinessLead, LeadDataError, deduplicate_leads, merge_leads
from app.models import Lead
from app.repositories import LeadRepository
from tests.test_lead_persistence import acme, in_new_session, persian

T1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 2, 6, 11, 30, tzinfo=UTC)
T3 = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)


def rows(session) -> int:
    return session.scalar(select(func.count()).select_from(Lead)) or 0


@pytest.fixture
def repo(session):
    return LeadRepository(session)


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'dedupe.db'}"


# ---------------------------------------------------------------- 1. identical lead twice
def test_identical_lead_twice_pure():
    assert merge_leads(acme(), acme()) == acme()
    assert deduplicate_leads([acme(), acme()]) == [acme()]


def test_identical_lead_twice_in_the_database(session, repo):
    first = repo.merge(acme())
    second = repo.merge(acme())
    assert first.created and not second.created and not second.changed
    assert rows(session) == 1
    assert repo.load("acme.example") == acme()


# ---------------------------------------------------------------- 2. same domain from different pages
def page(url, **fields):
    return BusinessLead(website="https://www.acme.example/", source_url=url, **fields)


def test_same_domain_from_different_pages_pure():
    home = page("https://www.acme.example/", business_name="Acme", page_type=PageCategory.HOMEPAGE, emails=["a@acme.example"])
    contact = page("https://acme.example/contact", page_type=PageCategory.CONTACT, emails=["b@acme.example"],
                   phones=[PhoneNumber("+14155550199", "415 555 0199")])
    result = deduplicate_leads([home, contact])
    assert len(result) == 1
    assert result[0].business_name == "Acme"
    assert result[0].emails == ("a@acme.example", "b@acme.example")
    assert [p.number for p in result[0].phones] == ["+14155550199"]
    assert result[0].source_url == "https://www.acme.example/" and result[0].page_type is PageCategory.HOMEPAGE


def test_same_domain_from_different_pages_in_the_database(session, repo):
    repo.merge(page("https://acme.example/", business_name="Acme", emails=["a@acme.example"]))
    repo.merge(BusinessLead(website="http://ACME.example:80/x", source_url="https://acme.example/about", emails=["b@acme.example"]))
    repo.merge(BusinessLead(website="https://www.acme.example/", source_url="https://acme.example/contact", emails=["c@acme.example"]))
    assert rows(session) == 1
    stored = repo.load("acme.example")
    assert stored.emails == ("a@acme.example", "b@acme.example", "c@acme.example")
    assert stored.website == "https://www.acme.example/"  # the first website is kept


# ---------------------------------------------------------------- 3. same lead after restart
def test_same_lead_after_restart(db_url):
    migrate.upgrade(db_url)
    in_new_session(db_url, lambda s, r: r.merge(acme()))
    again = in_new_session(db_url, lambda s, r: r.merge(acme()))  # a new engine and session = a new process
    assert not again.created and not again.changed
    assert in_new_session(db_url, lambda s, r: r.count()) == 1
    assert in_new_session(db_url, lambda s, r: r.load("acme.example")) == acme()


def test_new_page_after_restart_updates_the_same_lead(db_url):
    migrate.upgrade(db_url)
    in_new_session(db_url, lambda s, r: r.merge(acme()))
    extra = BusinessLead(website="https://acme.example/", source_url="https://acme.example/contact",
                         emails=["help@acme.example"], first_seen=T3, last_seen=T3)
    in_new_session(db_url, lambda s, r: r.merge(extra))
    in_new_session(db_url, lambda s, r: r.merge(extra))
    stored = in_new_session(db_url, lambda s, r: r.load("acme.example"))
    assert stored.emails == ("help@acme.example", "info@acme.example", "sales@acme.example")
    assert (stored.first_seen, stored.last_seen) == (T1, T3)
    assert in_new_session(db_url, lambda s, r: r.count()) == 1


# ---------------------------------------------------------------- 4. missing fields added later
def test_missing_fields_are_added_later():
    sparse = BusinessLead(website="https://acme.example/", first_seen=T1, last_seen=T1)
    full = acme(first_seen=T2, last_seen=T2)
    merged = merge_leads(sparse, full)
    assert merged.business_name == "Acme Plumbing" and merged.description == "Plumbers in Springfield"
    assert merged.address == full.address and merged.language == "en"
    assert merged.page_type is PageCategory.HOMEPAGE and merged.source_url == "https://acme.example/"
    assert merged.emails == full.emails and merged.phones == full.phones and merged.social_profiles == full.social_profiles
    assert (merged.first_seen, merged.last_seen) == (T1, T2)


def test_missing_fields_are_added_later_in_the_database(session, repo):
    repo.merge(BusinessLead(website="https://acme.example/"))
    result = repo.merge(acme())
    assert result.changed and not result.created and rows(session) == 1
    assert repo.load("acme.example") == acme()


def test_unknown_language_is_a_gap():
    assert merge_leads(acme(language="unknown"), acme(language="fa")).language == "fa"
    assert merge_leads(acme(language="en"), acme(language="fa")).language == "en"


# ---------------------------------------------------------------- 5. existing populated fields preserved
def test_existing_populated_fields_are_preserved():
    other = BusinessLead(
        website="https://www.acme.example/other", business_name="Other Name", description="Other text",
        address=Address(street="9 Side St", city="Elsewhere"), page_type=PageCategory.CONTACT, language="fa",
        source_url="https://acme.example/contact", first_seen=T3, last_seen=T3,
    )
    merged = merge_leads(acme(), other)
    kept = acme()
    assert (merged.website, merged.business_name, merged.description) == (kept.website, kept.business_name, kept.description)
    assert merged.address == kept.address  # never mixed with the other address
    assert (merged.page_type, merged.language, merged.source_url) == (kept.page_type, kept.language, kept.source_url)


def test_a_newer_homepage_does_not_override_the_stored_values(session, repo):
    repo.merge(acme(business_name="Stored Name", page_type=PageCategory.ABOUT, source_url="https://acme.example/about"))
    repo.merge(acme(business_name="New Homepage Name", description="New text", first_seen=T3, last_seen=T3))
    stored = repo.load("acme.example")
    assert stored.business_name == "Stored Name" and stored.description == "Plumbers in Springfield"
    assert stored.page_type is PageCategory.ABOUT and stored.source_url == "https://acme.example/about"
    assert (stored.first_seen, stored.last_seen) == (T1, T3)


def test_existing_spelling_of_shared_values_is_kept():
    a = acme(phones=[PhoneNumber("+14155550199", "ZZZ")], social_profiles=[SocialLink("x", "https://x.com/Acme")])
    b = acme(phones=[PhoneNumber("+14155550199", "AAA")], social_profiles=[SocialLink("x", "https://x.com/acme")])
    merged = merge_leads(a, b)
    assert [p.raw for p in merged.phones] == ["ZZZ"]
    assert [s.url for s in merged.social_profiles if s.platform == "x"] == ["https://x.com/Acme"]


def test_merge_does_not_modify_its_inputs():
    a, b = acme(), BusinessLead(website="https://acme.example/", emails=["z@acme.example"])
    merge_leads(a, b)
    assert a == acme() and b.emails == ("z@acme.example",)


# ---------------------------------------------------------------- 6. different domains remain separate
def test_different_domains_remain_separate(session, repo):
    repo.merge(acme())
    repo.merge(persian())
    repo.merge(acme(website="https://acme-plumbing.example/", source_url=None))  # similar name, other domain
    repo.merge(acme(website="https://acme.example.org/", source_url=None))
    assert rows(session) == 4
    assert repo.load("acme.example") == acme()


def test_deduplicate_keeps_distinct_sites_ordered_by_identity():
    a, p = acme(), persian()
    near = acme(website="https://acme-plumbing.example/", source_url=None)
    forward = deduplicate_leads([a, p, near, a])
    assert len(forward) == 3
    assert [x.identity.key for x in forward] == sorted(x.identity.key for x in forward)
    assert deduplicate_leads([near, p, a]) == forward  # arrival order of different sites does not matter


def test_hosting_platform_tenants_are_not_merged():
    a = BusinessLead(website="https://one.github.io/", business_name="One")
    b = BusinessLead(website="https://two.github.io/", business_name="Two")
    assert len(deduplicate_leads([a, b])) == 2


def test_merging_different_identities_raises():
    with pytest.raises(LeadDataError):
        merge_leads(acme(), persian())


def test_non_leads_are_rejected():
    with pytest.raises(LeadDataError):
        merge_leads(acme(), {"website": "https://acme.example/"})  # type: ignore[arg-type]
    with pytest.raises(LeadDataError):
        deduplicate_leads([acme(), None])  # type: ignore[list-item]


# ---------------------------------------------------------------- determinism
def test_merge_is_idempotent_and_repeatable():
    extra = BusinessLead(website="https://acme.example/", emails=["x@acme.example"], first_seen=T3, last_seen=T3)
    once = merge_leads(acme(), extra)
    assert merge_leads(once, extra) == once
    assert merge_leads(acme(), extra).to_json() == once.to_json()
    assert deduplicate_leads([]) == []
