"""Phase 8.4: lead persistence (LeadRepository + `leads` table + migration 0005).

File-database tests use fresh engines and sessions to imitate a restart. The one crawl test uses a loopback
server (no internet).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.db import migrate
from app.db.session import create_db_engine
from app.leads import BusinessLead, PageSource, aggregate_parsed_pages
from app.models import Crawl, Domain, Lead, Page
from app.repositories import CrawlRepository, DomainRepository, LeadRepository, PageRepository
from tests.test_crawler_bfs import local_network, make_crawler  # noqa: F401 (fixture)
from tests.test_crawler_http_client import HTML_HEADERS, server

T1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 2, 6, 11, 30, tzinfo=UTC)
T3 = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)


def acme(**fields) -> BusinessLead:
    values = dict(
        website="https://acme.example/",
        business_name="Acme Plumbing",
        description="Plumbers in Springfield",
        emails=["info@acme.example", "sales@acme.example"],
        phones=[PhoneNumber("+14155550199", "+1 415 555 0199")],
        address=Address(street="1 Main St", city="Springfield", country="US", formatted="1 Main St, Springfield, US"),
        social_profiles=[SocialLink("x", "https://x.com/acme"), SocialLink("linkedin", "https://www.linkedin.com/company/acme")],
        page_type=PageCategory.HOMEPAGE,
        language="en",
        source_url="https://acme.example/",
        first_seen=T1,
        last_seen=T2,
    )
    values.update(fields)
    return BusinessLead(**values)


def persian() -> BusinessLead:
    return BusinessLead(
        website="https://arman.example/",
        business_name="لوله‌کشی آرمان",
        description="خدمات لوله‌کشی ساختمان در تهران",
        emails=["info@arman.example"],
        phones=[PhoneNumber("+982112345678", "۰۲۱-۱۲۳۴۵۶۷۸")],
        address=Address(street="خیابان ولیعصر", city="تهران", country="ایران", formatted="خیابان ولیعصر، تهران، ایران"),
        social_profiles=[SocialLink("instagram", "https://www.instagram.com/arman")],
        page_type=PageCategory.ABOUT,
        language="fa",
        source_url="https://arman.example/about",
        first_seen=T1,
        last_seen=T1,
    )


@pytest.fixture
def leads(session):
    return LeadRepository(session)


def lead_rows(session) -> int:
    return session.scalar(select(func.count()).select_from(Lead)) or 0


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'leads.db'}"


def in_new_session(url, work):
    """Run `work(session, repo)` on its own engine/session (like a fresh process) and commit."""
    engine = create_db_engine(url)
    try:
        with Session(engine) as s:
            result = work(s, LeadRepository(s))
            s.commit()
            return result
    finally:
        engine.dispose()


# ---------------------------------------------------------------- 1. create lead
def test_create_lead(session, leads):
    result = leads.save(acme())
    assert result.created and result.changed
    row = result.record
    assert row.id is not None and row.domain.name == "acme.example"
    assert row.website == "https://acme.example/" and row.business_name == "Acme Plumbing"
    assert row.emails == ["info@acme.example", "sales@acme.example"]
    assert lead_rows(session) == 1 and leads.count() == 1


def test_create_minimal_lead(session, leads):
    result = leads.save(BusinessLead(website="https://min.example"))
    assert result.created
    loaded = leads.load("min.example")
    assert loaded == BusinessLead(website="https://min.example")
    assert loaded.emails == () and loaded.address is None and loaded.first_seen is None


def test_create_reuses_an_existing_domain_row(session, leads):
    domain, _ = DomainRepository(session).get_or_create("acme.example")
    leads.save(acme())
    assert session.scalar(select(func.count()).select_from(Domain)) == 1
    assert leads.get_by_domain("acme.example").domain_id == domain.id


# ---------------------------------------------------------------- 2. read lead
def test_read_lead_round_trips_every_field(session, leads):
    original = acme()
    leads.save(original)
    session.expire_all()
    loaded = leads.load("acme.example")
    assert loaded == original
    assert loaded.to_json() == original.to_json()
    assert loaded.first_seen == T1 and loaded.last_seen == T2 and loaded.first_seen.tzinfo is not None


def test_read_by_domain_is_case_and_space_insensitive(session, leads):
    leads.save(acme())
    assert leads.load(" ACME.Example ") == acme()


def test_load_all_is_ordered_by_row(session, leads):
    leads.save(acme())
    leads.save(persian())
    assert [lead.domain for lead in leads.load_all()] == ["acme.example", "arman.example"]
    assert [lead.domain for lead in leads.load_all(limit=1, offset=1)] == ["arman.example"]


# ---------------------------------------------------------------- 3. update lead
def test_update_lead_replaces_the_values(session, leads):
    first = leads.save(acme())
    changed = acme(
        business_name="Acme Plumbing & Heating",
        emails=["new@acme.example"],
        phones=[],
        address=None,
        social_profiles=[],
        language="fa",
        page_type=PageCategory.CONTACT,
        source_url="https://acme.example/contact",
    )
    second = leads.save(changed)
    assert not second.created and second.changed and second.record.id == first.record.id
    session.expire_all()
    loaded = leads.load("acme.example")
    assert loaded.business_name == "Acme Plumbing & Heating"
    assert loaded.emails == ("new@acme.example",) and loaded.phones == () and loaded.address is None
    assert loaded.social_profiles == () and loaded.language == "fa" and loaded.page_type is PageCategory.CONTACT
    assert lead_rows(session) == 1


def test_update_touches_updated_at_but_not_created_at(session, leads):
    row = leads.save(acme()).record
    session.commit()
    created, before = row.created_at, row.updated_at
    leads.save(acme(business_name="Other Name"))
    session.commit()
    assert row.created_at == created and row.updated_at >= before


def test_seen_range_never_shrinks(session, leads):
    leads.save(acme(first_seen=T2, last_seen=T2))
    leads.save(acme(first_seen=T3, last_seen=T3))  # later data: last_seen moves forward, first_seen stays
    stored = leads.load("acme.example")
    assert (stored.first_seen, stored.last_seen) == (T2, T3)
    leads.save(acme(first_seen=T1, last_seen=T1))  # older data: first_seen moves back, last_seen stays
    stored = leads.load("acme.example")
    assert (stored.first_seen, stored.last_seen) == (T1, T3)
    leads.save(acme(first_seen=None, last_seen=None))  # no times: stored ones are kept
    stored = leads.load("acme.example")
    assert (stored.first_seen, stored.last_seen) == (T1, T3)


def test_merge_combines_with_the_stored_lead(session, leads):
    leads.save(acme())
    extra = BusinessLead(
        website="https://acme.example/", source_url="https://acme.example/contact", page_type=PageCategory.CONTACT,
        business_name="Contact Name", emails=["Sales@Acme.example", "help@acme.example"],
        phones=[PhoneNumber("+14155550134", "+1 415 555 0134")], social_profiles=[SocialLink("x", "https://x.com/Acme")],
        first_seen=T3, last_seen=T3,
    )
    result = leads.merge(extra)
    assert not result.created and result.changed
    merged = leads.load("acme.example")
    assert merged.emails == ("help@acme.example", "info@acme.example", "sales@acme.example")
    assert [p.number for p in merged.phones] == ["+14155550134", "+14155550199"]
    assert len(merged.social_profiles) == 2  # the x profile exists once
    assert merged.business_name == "Acme Plumbing"  # stored homepage lead wins
    assert (merged.first_seen, merged.last_seen) == (T1, T3)
    assert leads.merge(extra).changed is False  # merging the same data again changes nothing
    assert lead_rows(session) == 1


def test_merge_into_an_empty_store_creates(session, leads):
    result = leads.merge(acme())
    assert result.created and leads.load("acme.example") == acme()


# ---------------------------------------------------------------- 4. repeated save
def test_repeated_save_is_a_no_op(session, leads):
    first = leads.save(acme())
    session.commit()
    stamp = first.record.updated_at
    for _ in range(3):
        again = leads.save(acme())
        assert not again.created and not again.changed and again.record.id == first.record.id
    session.commit()
    assert first.record.updated_at == stamp
    assert lead_rows(session) == 1 and leads.load("acme.example") == acme()


def test_repeated_save_of_a_reloaded_lead_is_a_no_op(session, leads):
    leads.save(persian())
    session.commit()
    session.expire_all()
    assert leads.save(leads.load("arman.example")).changed is False


# ---------------------------------------------------------------- 5. duplicate prevention
def test_one_lead_per_domain_even_for_different_websites_of_the_domain(session, leads):
    leads.save(acme())
    other = acme(website="https://www.acme.example/", source_url="https://www.acme.example/")
    result = leads.save(other)
    assert not result.created
    assert lead_rows(session) == 1
    assert leads.load("acme.example").website == "https://www.acme.example/"


def test_different_domains_get_different_leads(session, leads):
    leads.save(acme())
    leads.save(persian())
    assert lead_rows(session) == 2


def test_database_refuses_a_second_row_for_a_domain(session, leads):
    row = leads.save(acme()).record
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Lead(domain_id=row.domain_id, website="https://acme.example/", emails=[], phones=[], social_profiles=[]))
            session.flush()
    assert lead_rows(session) == 1


def test_save_recovers_when_the_row_appears_between_select_and_insert(session, leads, monkeypatch):
    """A concurrent writer wins the race: the unique domain_id fires and the save becomes an update."""
    leads.save(acme(business_name="Original"))
    real = LeadRepository.add
    calls = []

    def racing_add(self, obj):
        calls.append(obj)
        return real(self, obj)

    monkeypatch.setattr(LeadRepository, "add", racing_add)
    original_scalars = session.scalars
    state = {"hide": True}

    class Empty:
        def first(self):
            return None

    def hiding(stmt, *args, **kwargs):
        if state["hide"] and "FROM leads" in str(stmt):
            state["hide"] = False
            return Empty()
        return original_scalars(stmt, *args, **kwargs)

    monkeypatch.setattr(session, "scalars", hiding)
    result = leads.save(acme(business_name="Winner"))
    assert calls and not result.created
    assert lead_rows(session) == 1 and leads.load("acme.example").business_name == "Winner"


def test_invalid_crawl_reference_creates_nothing(session, leads):
    with pytest.raises(IntegrityError):
        leads.save(acme(), crawl_id=999)
    session.rollback()
    assert lead_rows(session) == 0


# ---------------------------------------------------------------- 6. persistence after restart
def test_lead_survives_a_restart(db_url):
    migrate.upgrade(db_url)
    saved = in_new_session(db_url, lambda s, r: r.save(acme()).record.id)
    loaded = in_new_session(db_url, lambda s, r: r.load("acme.example"))
    assert loaded == acme()
    assert in_new_session(db_url, lambda s, r: r.get_by_domain("acme.example").id) == saved


def test_save_after_restart_updates_instead_of_duplicating(db_url):
    migrate.upgrade(db_url)
    in_new_session(db_url, lambda s, r: r.save(acme()))
    again = in_new_session(db_url, lambda s, r: r.save(acme()))
    assert not again.created and not again.changed
    updated = in_new_session(db_url, lambda s, r: r.save(acme(business_name="Renamed")))
    assert not updated.created and updated.changed
    assert in_new_session(db_url, lambda s, r: r.count()) == 1
    assert in_new_session(db_url, lambda s, r: r.load("acme.example")).business_name == "Renamed"


def test_uncommitted_save_is_not_persisted(db_url):
    migrate.upgrade(db_url)
    engine = create_db_engine(db_url)
    try:
        with Session(engine) as s:
            LeadRepository(s).save(acme())
            s.rollback()  # repositories never commit
    finally:
        engine.dispose()
    assert in_new_session(db_url, lambda s, r: r.count()) == 0


def test_resume_adds_to_the_stored_lead_without_losing_anything(db_url):
    migrate.upgrade(db_url)
    in_new_session(db_url, lambda s, r: r.merge(acme(first_seen=T1, last_seen=T1)))
    later = BusinessLead(
        website="https://acme.example/", source_url="https://acme.example/contact", emails=["help@acme.example"],
        first_seen=T3, last_seen=T3,
    )
    in_new_session(db_url, lambda s, r: r.merge(later))
    in_new_session(db_url, lambda s, r: r.merge(later))  # a repeated resume changes nothing more
    stored = in_new_session(db_url, lambda s, r: r.load("acme.example"))
    assert stored.emails == ("help@acme.example", "info@acme.example", "sales@acme.example")
    assert (stored.first_seen, stored.last_seen) == (T1, T3)
    assert in_new_session(db_url, lambda s, r: r.count()) == 1


# ---------------------------------------------------------------- 7. empty database
def test_empty_database(session, leads):
    assert leads.count() == 0
    assert leads.load("acme.example") is None
    assert leads.get_by_domain("acme.example") is None
    assert leads.load_all() == []
    assert leads.list() == []


def test_migrated_empty_database_has_the_leads_table(db_url):
    migrate.upgrade(db_url)
    assert in_new_session(db_url, lambda s, r: r.count()) == 0
    engine = create_db_engine(db_url)
    try:
        insp = inspect(engine)
        assert "leads" in insp.get_table_names()
        assert {"ix_leads_crawl_id", "ix_leads_business_name"} <= {i["name"] for i in insp.get_indexes("leads")}
    finally:
        engine.dispose()


def test_downgrade_removes_the_table_and_keeps_the_rest(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url, "0004")
    engine = create_db_engine(db_url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert "leads" not in names and {"domains", "crawls", "pages"} <= names


# ---------------------------------------------------------------- 8. Unicode / Persian data
def test_persian_lead_round_trips_exactly(session, leads):
    original = persian()
    leads.save(original)
    session.commit()
    session.expire_all()
    loaded = leads.load("arman.example")
    assert loaded == original
    assert loaded.business_name == "لوله‌کشی آرمان"  # ZWNJ kept
    assert "\u200c" in loaded.business_name and "\u200c" in loaded.description
    assert loaded.phones[0].raw == "۰۲۱-۱۲۳۴۵۶۷۸" and loaded.phones[0].number == "+982112345678"
    assert loaded.address.city == "تهران" and loaded.language == "fa"


def test_persian_lead_survives_a_restart_and_a_repeated_save(db_url):
    migrate.upgrade(db_url)
    in_new_session(db_url, lambda s, r: r.save(persian()))
    again = in_new_session(db_url, lambda s, r: r.save(persian()))
    assert not again.changed
    assert in_new_session(db_url, lambda s, r: r.load("arman.example")) == persian()


def test_idn_domain_is_stored_as_punycode(session, leads):
    lead = BusinessLead(website="https://آرمان.ir/", business_name="آرمان")
    assert lead.domain.startswith("xn--")
    leads.save(lead)
    assert leads.load(lead.domain).business_name == "آرمان"
    assert DomainRepository(session).get_by_name(lead.domain) is not None


def test_mixed_scripts_and_emoji_in_text(session, leads):
    lead = acme(business_name="Acme آرمان ☕", description="سلام hello 🙂")
    leads.save(lead)
    session.expire_all()
    assert leads.load("acme.example") == lead


# ---------------------------------------------------------------- 9. interaction with crawl persistence
def new_crawl(session, domain="127.0.0.1"):
    row, _ = DomainRepository(session).get_or_create(domain)
    return CrawlRepository(session).create(row.id, seed_url=f"http://{domain}")


def test_lead_links_to_its_crawl_and_survives_crawl_deletion(session, leads):
    crawl = new_crawl(session, "acme.example")
    leads.save(acme(), crawl_id=crawl.id)
    assert leads.get_by_domain("acme.example").crawl_id == crawl.id
    leads.save(acme())  # no crawl_id: the link is kept
    assert leads.get_by_domain("acme.example").crawl_id == crawl.id
    session.delete(crawl)
    session.flush()
    session.expire_all()
    row = leads.get_by_domain("acme.example")
    assert row is not None and row.crawl_id is None  # ON DELETE SET NULL
    assert leads.load("acme.example") == acme()


def test_deleting_the_domain_deletes_its_lead_only(session, leads):
    leads.save(acme())
    leads.save(persian())
    session.delete(DomainRepository(session).get_by_name("acme.example"))
    session.flush()
    session.expire_all()
    assert leads.load("acme.example") is None and leads.load("arman.example") == persian()


def test_saving_leads_does_not_change_crawl_or_page_rows(session, leads):
    crawl = new_crawl(session, "acme.example")
    pages = PageRepository(session)
    page = pages.create(crawl.id, "https://acme.example/")
    session.flush()
    before = (crawl.status, crawl.pages_crawled, crawl.updated_at, page.status, page.updated_at, pages.count(), session.scalar(select(func.count()).select_from(Crawl)))
    leads.save(acme(), crawl_id=crawl.id)
    leads.save(acme(), crawl_id=crawl.id)
    session.flush()
    after = (crawl.status, crawl.pages_crawled, crawl.updated_at, page.status, page.updated_at, pages.count(), session.scalar(select(func.count()).select_from(Crawl)))
    assert before == after


def test_a_real_crawl_then_lead_persistence_then_restart(local_network, db_url):
    """Crawl a loopback site into the database, aggregate its parsed pages into a lead, store it, restart."""
    from app.db.base import Base

    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        crawl_id = new_crawl(s).id
        s.commit()
    engine.dispose()

    routes = {
        "/": (200, b"<html lang='en'><head><title>Acme Plumbing</title></head><body><h1>Acme Plumbing</h1>"
                   b"<a href='/contact'>c</a></body></html>", HTML_HEADERS),
        "/contact": (200, b"<html lang='en'><head><title>Contact us | Acme Plumbing</title></head>"
                          b"<body>Email info@acme.example. <a href='https://x.com/acme'>x</a></body></html>", HTML_HEADERS),
    }

    def crawl_and_store(base):
        engine = create_db_engine(db_url)
        try:
            with Session(engine) as s:
                async def main():
                    async with make_crawler(base, page_repository=PageRepository(s), crawl_repository=CrawlRepository(s)) as crawler:
                        return await crawler.crawl(base + "/", crawl_id=crawl_id)

                result = asyncio.run(main())
                sources = [PageSource(p.parsed, p.url) for p in result.pages if p.parsed is not None]
                lead = aggregate_parsed_pages(sources)
                outcome = LeadRepository(s).save(lead, crawl_id=crawl_id)
                s.commit()
                return lead, outcome
        finally:
            engine.dispose()

    with server(routes) as (base, state):
        lead, outcome = crawl_and_store(base)
        assert outcome.created and lead.business_name == "Acme Plumbing" and lead.emails == ("info@acme.example",)
        assert len(state.requests) == 2  # each page fetched once

    again = in_new_session(db_url, lambda s, r: r.save(lead))  # restart: the same lead again is a no-op
    assert not again.created and not again.changed

    engine = create_db_engine(db_url)
    try:
        with Session(engine) as s:
            stored = LeadRepository(s).load(lead.domain)
            assert stored == lead
            assert LeadRepository(s).count() == 1
            assert s.scalar(select(func.count()).select_from(Page).where(Page.crawl_id == crawl_id)) == 2
            crawl = CrawlRepository(s).require(crawl_id)
            assert crawl.status.value == "completed"
            assert LeadRepository(s).get_by_domain(lead.domain).crawl_id == crawl_id
    finally:
        engine.dispose()
