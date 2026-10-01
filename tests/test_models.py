from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
from app.models import (
    ApiKey,
    Crawl,
    CrawlStatus,
    Domain,
    Email,
    EmailEvidence,
    EmailEvidenceSource,
    EmailPattern,
    EmailPatternType,
    EmailVerification,
    Page,
    PageStatus,
    Person,
    PersonEvidence,
    VerificationMethod,
    VerificationStatus,
)

EXPECTED_TABLES = {
    "domains", "crawls", "pages", "emails", "email_evidence", "people",
    "person_evidence", "email_verifications", "email_patterns", "api_keys", "leads",
}


def count(session, model) -> int:
    return len(session.scalars(select(model)).all())


def make_graph(session):
    """Domain -> crawl -> page -> email(+evidence,+verification) and person(+evidence), pattern."""
    domain = Domain(name="Example.COM ")
    crawl = Crawl(domain=domain)
    page = Page(crawl=crawl, url="https://example.com/team", url_hash=Page.hash_url("https://example.com/team"))
    email = Email(domain=domain, address="Jane.Doe@Example.com")
    email.evidence.append(EmailEvidence(page=page, source_type=EmailEvidenceSource.MAILTO_LINK))
    email.verifications.append(EmailVerification(method=VerificationMethod.DNS_MX))
    person = Person(domain=domain, email=email, full_name="Jane Doe", first_name="Jane", last_name="Doe")
    person.evidence.append(PersonEvidence(page=page))
    domain.email_patterns.append(EmailPattern(pattern=EmailPatternType.FIRST_LAST, sample_count=3, confidence=0.9))
    session.add(domain)
    session.commit()
    return domain, crawl, page, email, person


def test_all_tables_registered_and_mappers_configure():
    configure_mappers()
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_graph_persists_with_defaults_and_normalization(session):
    domain, crawl, page, email, person = make_graph(session)
    assert domain.name == "example.com"
    assert email.address == "jane.doe@example.com"
    assert email.local_part == "jane.doe"
    assert crawl.status is CrawlStatus.PENDING
    assert (crawl.max_pages, crawl.max_depth, crawl.pages_crawled) == (100, 2, 0)
    assert page.status is PageStatus.PENDING and page.depth == 0
    assert email.is_role_based is False
    assert email.verifications[0].status is VerificationStatus.UNKNOWN
    assert person.email is email and email.people == [person]
    assert page.email_evidence[0].email is email
    assert page.person_evidence[0].person is person
    assert domain.email_patterns[0].pattern is EmailPatternType.FIRST_LAST


def test_timestamps_set_and_updated_at_changes(session):
    domain, *_ = make_graph(session)
    assert domain.created_at is not None and domain.updated_at is not None
    before = domain.updated_at
    domain.last_crawled_at = datetime.now(UTC)
    session.commit()
    session.refresh(domain)
    assert domain.updated_at >= before


def test_enums_stored_as_values(session):
    make_graph(session)
    assert session.execute(text("select status from crawls")).scalar_one() == "pending"
    assert session.execute(text("select pattern from email_patterns")).scalar_one() == "first.last"


def test_invalid_enum_rejected_by_database(session):
    domain, *_ = make_graph(session)
    with pytest.raises(IntegrityError):
        session.execute(text("update crawls set status = 'bogus'"))
    session.rollback()


def test_domain_name_unique_and_lowercase_check(session):
    session.add(Domain(name="a.com"))
    session.commit()
    session.add(Domain(name="A.com"))  # normalised to a.com
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(text("insert into domains (name) values ('UPPER.com')"))
    session.rollback()


def test_email_unique_per_domain_but_allowed_across_domains(session):
    d1, d2 = Domain(name="a.com"), Domain(name="b.com")
    session.add_all([d1, d2])
    session.flush()
    session.add_all([Email(domain=d1, address="x@gmail.com"), Email(domain=d2, address="x@gmail.com")])
    session.commit()
    session.add(Email(domain=d1, address="x@gmail.com"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_email_format_check(session):
    d = Domain(name="a.com")
    session.add(d)
    session.flush()
    with pytest.raises(IntegrityError):
        session.execute(
            text("insert into emails (domain_id, address, local_part) values (:d, 'nope', 'nope')"),
            {"d": d.id},
        )
    session.rollback()


def test_page_unique_per_crawl_url_hash(session):
    _, crawl, page, *_ = make_graph(session)
    session.add(Page(crawl=crawl, url=page.url, url_hash=page.url_hash))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_page_and_crawl_check_constraints(session):
    _, crawl, *_ = make_graph(session)
    bad_pages = [
        {"depth": -1, "http_status": 200},
        {"depth": 0, "http_status": 999},
    ]
    for i, kw in enumerate(bad_pages):
        session.add(Page(crawl=crawl, url=f"u{i}", url_hash=f"h{i}", **kw))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    crawl = session.scalars(select(Crawl)).one()
    crawl.started_at = datetime(2026, 1, 2, tzinfo=UTC)
    crawl.finished_at = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "model_kwargs",
    [
        lambda p, e: EmailEvidence(email=e, page=p, source_type=EmailEvidenceSource.OTHER, confidence=1.5),
        lambda p, e: EmailEvidence(email=e, page=p, source_type=EmailEvidenceSource.OTHER, occurrences=0),
    ],
)
def test_evidence_check_constraints(session, model_kwargs):
    _, _, page, email, _ = make_graph(session)
    session.add(model_kwargs(page, email))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_verification_smtp_code_and_pattern_constraints(session):
    domain, _, _, email, _ = make_graph(session)
    session.add(EmailVerification(email=email, method=VerificationMethod.SMTP, smtp_code=42))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(EmailPattern(domain_id=domain.id, pattern=EmailPatternType.FIRST_LAST))  # duplicate
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(EmailPattern(domain_id=domain.id, pattern=EmailPatternType.FLAST, confidence=2))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_deleting_domain_cascades_everything(session):
    domain, *_ = make_graph(session)
    session.delete(domain)
    session.commit()
    for model in (Domain, Crawl, Page, Email, EmailEvidence, Person, PersonEvidence,
                  EmailVerification, EmailPattern):
        assert count(session, model) == 0, model.__name__


def test_db_level_cascade_without_orm(session):
    """passive_deletes relies on real FK cascades (SQLite FKs are enabled by the engine helper)."""
    make_graph(session)
    session.execute(text("delete from domains"))
    session.commit()
    assert count(session, Page) == 0 and count(session, EmailEvidence) == 0


def test_deleting_crawl_removes_pages_and_evidence_but_keeps_emails(session):
    _, crawl, *_ = make_graph(session)
    session.delete(crawl)
    session.commit()
    assert count(session, Page) == 0
    assert count(session, EmailEvidence) == 0 and count(session, PersonEvidence) == 0
    assert count(session, Email) == 1 and count(session, Person) == 1


def test_deleting_email_keeps_person_with_null_email(session):
    _, _, _, email, person = make_graph(session)
    session.delete(email)
    session.commit()
    session.refresh(person)
    assert person.email_id is None
    assert count(session, EmailEvidence) == 0 and count(session, EmailVerification) == 0


def test_deleting_api_key_keeps_crawl_with_null_key(session):
    domain = Domain(name="a.com")
    key = ApiKey(name="k", key_prefix="abc123", key_hash="h" * 64)
    crawl = Crawl(domain=domain, api_key=key)
    session.add(crawl)
    session.commit()
    session.delete(key)
    session.commit()
    session.refresh(crawl)
    assert crawl.api_key_id is None and count(session, Crawl) == 1


def test_api_key_unique_and_usable(session):
    session.add(ApiKey(name="k1", key_prefix="p1", key_hash="h1"))
    session.commit()
    session.add(ApiKey(name="k2", key_prefix="p1", key_hash="h2"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    key = session.scalars(select(ApiKey)).one()
    assert key.is_active is True and key.is_usable()
    key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not key.is_usable()
    key.expires_at = None
    key.revoked_at = datetime.now(UTC)
    assert not key.is_usable()


def test_foreign_keys_enforced(session):
    with pytest.raises(IntegrityError):
        session.execute(text("insert into crawls (domain_id) values (9999)"))
    session.rollback()


def test_expected_indexes_exist(engine):
    insp = inspect(engine)
    names = {t: {i["name"] for i in insp.get_indexes(t)} for t in EXPECTED_TABLES}
    assert "ix_crawls_domain_id_status" in names["crawls"]
    assert "ix_pages_crawl_id_status" in names["pages"]
    assert "ix_emails_address" in names["emails"]
    assert "ix_email_evidence_page_id" in names["email_evidence"]
    assert "ix_people_domain_id_last_first" in names["people"]
    assert "ix_email_verifications_email_id_checked_at" in names["email_verifications"]


def test_ddl_compiles_for_postgresql():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=dialect))
