from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError

from app.models import (
    Crawl,
    CrawlStatus,
    Domain,
    EmailEvidenceSource,
    EmailPatternType,
    PageStatus,
    PersonEvidenceSource,
    VerificationMethod,
    VerificationStatus,
)
from app.repositories import (
    ApiKeyRepository,
    CrawlRepository,
    DomainRepository,
    EmailPatternRepository,
    EmailRepository,
    EmailVerificationRepository,
    InvalidTransitionError,
    NotFoundError,
    PageRepository,
    PersonRepository,
)


@pytest.fixture
def repos(session):
    class R:
        domains = DomainRepository(session)
        crawls = CrawlRepository(session)
        pages = PageRepository(session)
        emails = EmailRepository(session)
        people = PersonRepository(session)
        verifications = EmailVerificationRepository(session)
        patterns = EmailPatternRepository(session)
        api_keys = ApiKeyRepository(session)

    R.session = session
    return R


@pytest.fixture
def seeded(repos):
    domain, _ = repos.domains.get_or_create("Example.com")
    crawl = repos.crawls.create(domain.id, seed_url="https://example.com")
    page = repos.pages.create(crawl.id, "https://example.com/team")
    email, _ = repos.emails.get_or_create(domain.id, "Jane@Example.com")
    repos.emails.add_evidence(email.id, page.id, source_type=EmailEvidenceSource.MAILTO_LINK)
    repos.verifications.record(email.id, VerificationMethod.DNS_MX, VerificationStatus.VALID)
    person = repos.people.create(domain.id, "Jane Doe", first_name="Jane", last_name="Doe", email_id=email.id)
    repos.people.add_evidence(person.id, page.id, source_type=PersonEvidenceSource.TEAM_LISTING)
    repos.patterns.upsert(domain.id, EmailPatternType.FIRST, sample_count=2, confidence=0.5)
    repos.session.commit()
    return domain, crawl, page, email, person


# ---------------------------------------------------------------- base CRUD
def test_get_require_list_count_delete(repos):
    d = repos.domains.add(Domain(name="a.com"))
    repos.domains.add(Domain(name="b.com"))
    assert repos.domains.get(d.id) is d
    assert repos.domains.get(9999) is None
    with pytest.raises(NotFoundError):
        repos.domains.require(9999)
    assert repos.domains.count() == 2
    assert [x.name for x in repos.domains.list()] == ["a.com", "b.com"]
    assert [x.name for x in repos.domains.list(limit=1, offset=1)] == ["b.com"]
    assert repos.domains.delete_by_id(d.id) is True
    assert repos.domains.delete_by_id(d.id) is False
    assert repos.domains.count() == 1


def test_update_rejects_unknown_and_protected_fields(repos):
    d = repos.domains.add(Domain(name="a.com"))
    with pytest.raises(ValueError):
        repos.domains.update(d, bogus=1)
    with pytest.raises(ValueError):
        repos.domains.update(d, id=5)
    repos.domains.update(d, name="  NEW.com ")
    assert d.name == "new.com"  # model validator still applies


def test_repository_add_surfaces_integrity_errors(repos):
    repos.domains.add(Domain(name="a.com"))
    with pytest.raises(IntegrityError):
        repos.domains.add(Domain(name="a.com"))


# ------------------------------------------------------------------ domains
def test_domain_get_or_create_and_lookup_normalisation(repos):
    d1, created1 = repos.domains.get_or_create("Example.COM")
    d2, created2 = repos.domains.get_or_create(" example.com ")
    assert created1 is True and created2 is False and d1 is d2
    assert repos.domains.get_by_name("EXAMPLE.com") is d1
    assert repos.domains.get_by_name("nope.com") is None


def test_domain_get_or_create_recovers_from_race(repos, monkeypatch):
    """Simulate another writer inserting between our SELECT and INSERT."""
    existing = repos.domains.add(Domain(name="race.com"))
    calls = {"n": 0}
    real = repos.domains.get_by_name

    def flaky(name):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(name)

    monkeypatch.setattr(repos.domains, "get_by_name", flaky)
    domain, created = repos.domains.get_or_create("race.com")
    assert created is False and domain.id == existing.id
    assert repos.session.is_active  # savepoint rolled back, outer transaction still usable


def test_domain_search_and_mark_crawled(repos):
    for n in ("alpha.com", "beta.org", "alphabet.net"):
        repos.domains.get_or_create(n)
    assert [d.name for d in repos.domains.search("alpha")] == ["alpha.com", "alphabet.net"]
    assert len(repos.domains.search()) == 3
    assert repos.domains.search("100%") == []  # LIKE wildcards are escaped
    d = repos.domains.get_by_name("beta.org")
    when = datetime(2026, 1, 1, tzinfo=UTC)
    repos.domains.mark_crawled(d, when)
    assert d.last_crawled_at == when


# ------------------------------------------------------------------- crawls
def test_crawl_create_and_queries(repos):
    d, _ = repos.domains.get_or_create("a.com")
    c1 = repos.crawls.create(d.id, max_pages=5, max_depth=1)
    c2 = repos.crawls.create(d.id)
    repos.crawls.set_status(c2, CrawlStatus.RUNNING)
    assert (c1.max_pages, c1.max_depth, c1.status) == (5, 1, CrawlStatus.PENDING)
    assert [c.id for c in repos.crawls.list_for_domain(d.id)] == [c1.id, c2.id]
    assert [c.id for c in repos.crawls.list_for_domain(d.id, status=CrawlStatus.RUNNING)] == [c2.id]
    assert [c.id for c in repos.crawls.list_by_status(CrawlStatus.PENDING)] == [c1.id]
    assert repos.crawls.count_by_status() == {CrawlStatus.PENDING: 1, CrawlStatus.RUNNING: 1}


def test_crawl_status_lifecycle_sets_timestamps_and_domain(repos):
    d, _ = repos.domains.get_or_create("a.com")
    c = repos.crawls.create(d.id)
    t0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
    repos.crawls.set_status(c, CrawlStatus.RUNNING, now=t0)
    assert c.started_at == t0 and c.finished_at is None
    repos.crawls.increment_pages_crawled(c, 3)
    assert c.pages_crawled == 3
    t1 = t0 + timedelta(minutes=5)
    repos.crawls.set_status(c, CrawlStatus.COMPLETED, now=t1)
    assert c.finished_at == t1 and d.last_crawled_at == t1
    repos.session.commit()  # also satisfies finished_at >= started_at CHECK


def test_crawl_invalid_transitions_rejected(repos):
    d, _ = repos.domains.get_or_create("a.com")
    c = repos.crawls.create(d.id)
    with pytest.raises(InvalidTransitionError):
        repos.crawls.set_status(c, CrawlStatus.COMPLETED)  # pending -> completed
    repos.crawls.set_status(c, CrawlStatus.CANCELLED)
    assert c.started_at is not None and c.finished_at is not None
    with pytest.raises(InvalidTransitionError):
        repos.crawls.set_status(c, CrawlStatus.RUNNING)  # terminal


def test_crawl_failure_records_message(repos):
    d, _ = repos.domains.get_or_create("a.com")
    c = repos.crawls.create(d.id)
    repos.crawls.set_status(c, CrawlStatus.RUNNING)
    repos.crawls.set_status(c, CrawlStatus.FAILED, error_message="boom")
    assert c.error_message == "boom"


# -------------------------------------------------------------------- pages
def test_page_create_get_or_create_and_counts(repos):
    d, _ = repos.domains.get_or_create("a.com")
    c = repos.crawls.create(d.id)
    p, created = repos.pages.get_or_create(c.id, "https://a.com/x", depth=1)
    p2, created2 = repos.pages.get_or_create(c.id, "https://a.com/x")
    assert created and not created2 and p is p2 and p.depth == 1
    assert repos.pages.get_by_url(c.id, "https://a.com/x") is p
    assert repos.pages.get_by_url(c.id, "https://a.com/y") is None
    repos.pages.create(c.id, "https://a.com/y")
    repos.pages.mark_fetched(p, http_status=200, content_type="text/html", title="T")
    assert p.status is PageStatus.FETCHED and p.fetched_at is not None
    assert repos.pages.count_for_crawl(c.id) == 2
    assert repos.pages.count_for_crawl(c.id, status=PageStatus.FETCHED) == 1
    assert [x.url for x in repos.pages.list_for_crawl(c.id, status=PageStatus.PENDING)] == ["https://a.com/y"]


def test_page_mark_failed_and_same_url_allowed_in_other_crawl(repos):
    d, _ = repos.domains.get_or_create("a.com")
    c1, c2 = repos.crawls.create(d.id), repos.crawls.create(d.id)
    p1 = repos.pages.create(c1.id, "https://a.com/")
    repos.pages.create(c2.id, "https://a.com/")  # unique is per crawl
    repos.pages.mark_failed(p1, "timeout")
    assert p1.status is PageStatus.FAILED and p1.error_message == "timeout"
    with pytest.raises(IntegrityError):
        repos.pages.create(c1.id, "https://a.com/")


# ------------------------------------------------------------------- emails
def test_email_get_or_create_normalises_and_dedupes(repos):
    d, _ = repos.domains.get_or_create("a.com")
    e1, c1 = repos.emails.get_or_create(d.id, "Info@A.com", is_role_based=True)
    e2, c2 = repos.emails.get_or_create(d.id, " info@a.com ")
    assert c1 and not c2 and e1 is e2 and e1.address == "info@a.com" and e1.local_part == "info"
    assert repos.emails.get_by_address(d.id, "INFO@a.com") is e1


def test_email_list_filters_and_cross_domain_find(repos):
    d1, _ = repos.domains.get_or_create("a.com")
    d2, _ = repos.domains.get_or_create("b.com")
    repos.emails.get_or_create(d1.id, "info@a.com", is_role_based=True)
    repos.emails.get_or_create(d1.id, "jane@a.com")
    repos.emails.get_or_create(d2.id, "jane@a.com")
    assert len(repos.emails.list_for_domain(d1.id)) == 2
    assert [e.address for e in repos.emails.list_for_domain(d1.id, role_based=True)] == ["info@a.com"]
    assert [e.address for e in repos.emails.list_for_domain(d1.id, role_based=False)] == ["jane@a.com"]
    assert [e.address for e in repos.emails.list_for_domain(d1.id, search="INF")] == ["info@a.com"]
    assert len(repos.emails.find_by_address("Jane@A.com")) == 2


def test_email_evidence_upsert(repos, seeded):
    _, _, page, email, _ = seeded
    ev, created = repos.emails.add_evidence(
        email.id, page.id, source_type=EmailEvidenceSource.MAILTO_LINK, snippet="s", confidence=0.4
    )
    assert not created and ev.occurrences == 2 and ev.snippet == "s"
    ev2, created2 = repos.emails.add_evidence(
        email.id, page.id, source_type=EmailEvidenceSource.MAILTO_LINK, confidence=0.2
    )
    assert ev2 is ev and ev.confidence == 1.0 and ev.occurrences == 3  # keeps max confidence
    _, created3 = repos.emails.add_evidence(email.id, page.id, source_type=EmailEvidenceSource.OTHER)
    assert created3
    assert len(repos.emails.list_evidence(email.id)) == 2


def test_email_touch_seen(repos):
    d, _ = repos.domains.get_or_create("a.com")
    e, _ = repos.emails.get_or_create(d.id, "x@a.com")
    when = datetime(2026, 2, 2, tzinfo=UTC)
    repos.emails.touch_seen(e, when)
    assert e.last_seen_at == when


# ------------------------------------------------------------------- people
def test_person_crud_search_and_email_linking(repos):
    d1, _ = repos.domains.get_or_create("a.com")
    d2, _ = repos.domains.get_or_create("b.com")
    e1, _ = repos.emails.get_or_create(d1.id, "jane@a.com")
    e2, _ = repos.emails.get_or_create(d2.id, "jane@b.com")
    p = repos.people.create(d1.id, "  Jane Doe ", job_title="CEO")
    assert p.full_name == "Jane Doe" and p.email_id is None
    assert repos.people.find_by_name(d1.id, "jane DOE") == [p]
    assert repos.people.find_by_name(d2.id, "jane doe") == []
    repos.people.link_email(p, e1.id)
    assert p.email is e1
    with pytest.raises(ValueError):
        repos.people.link_email(p, e2.id)  # different domain
    with pytest.raises(NotFoundError):
        repos.people.link_email(p, 9999)
    repos.people.link_email(p, None)
    assert p.email_id is None
    assert repos.people.list_for_domain(d1.id, with_email=True) == [p]


def test_person_evidence_upsert(repos, seeded):
    _, _, page, _, person = seeded
    ev, created = repos.people.add_evidence(
        person.id, page.id, source_type=PersonEvidenceSource.TEAM_LISTING, snippet="x", confidence=0.3
    )
    assert not created and ev.snippet == "x"
    _, created2 = repos.people.add_evidence(person.id, page.id, source_type=PersonEvidenceSource.BYLINE)
    assert created2 and len(repos.people.list_evidence(person.id)) == 2


# ------------------------------------------------------------- verifications
def test_verification_history_newest_first(repos):
    d, _ = repos.domains.get_or_create("a.com")
    e, _ = repos.emails.get_or_create(d.id, "x@a.com")
    t = datetime(2026, 1, 1, tzinfo=UTC)
    repos.verifications.record(e.id, VerificationMethod.SYNTAX, VerificationStatus.VALID, checked_at=t)
    repos.verifications.record(
        e.id, VerificationMethod.SMTP, VerificationStatus.RISKY, smtp_code=250, is_catch_all=True,
        checked_at=t + timedelta(hours=1),
    )
    hist = repos.verifications.list_for_email(e.id)
    assert [v.method for v in hist] == [VerificationMethod.SMTP, VerificationMethod.SYNTAX]
    assert repos.verifications.latest_for_email(e.id).status is VerificationStatus.RISKY
    assert repos.verifications.latest_for_email(9999) is None


# ----------------------------------------------------------------- patterns
def test_pattern_upsert_and_ordering(repos):
    d, _ = repos.domains.get_or_create("a.com")
    p, created = repos.patterns.upsert(d.id, EmailPatternType.FIRST_LAST, sample_count=1, confidence=0.2)
    p2, created2 = repos.patterns.upsert(d.id, EmailPatternType.FIRST_LAST, sample_count=5, confidence=0.8)
    assert created and not created2 and p is p2 and (p.sample_count, p.confidence) == (5, 0.8)
    repos.patterns.upsert(d.id, EmailPatternType.FLAST, sample_count=1, confidence=0.9)
    assert [x.pattern for x in repos.patterns.list_for_domain(d.id)] == [
        EmailPatternType.FLAST,
        EmailPatternType.FIRST_LAST,
    ]
    assert repos.patterns.get_for_domain(d.id, EmailPatternType.LAST) is None


# ----------------------------------------------------------------- api keys
def test_api_key_lifecycle_stores_only_hash(repos):
    key, secret = repos.api_keys.create_new("ci")
    assert secret.startswith("lf_") and key.key_prefix in secret
    assert secret not in (key.key_hash, key.key_prefix, key.name)
    assert repos.api_keys.get_by_prefix(key.key_prefix) is key
    assert repos.api_keys.get_by_secret(secret) is key
    assert repos.api_keys.get_by_secret(secret + "x") is None
    assert repos.api_keys.get_usable_by_secret(secret) is key
    repos.api_keys.touch_last_used(key)
    assert key.last_used_at is not None
    repos.api_keys.revoke(key)
    assert repos.api_keys.get_usable_by_secret(secret) is None
    assert repos.api_keys.list_keys(active_only=True) == []
    assert repos.api_keys.list_keys() == [key]


def test_api_key_expiry(repos):
    key, secret = repos.api_keys.create_new("tmp", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert repos.api_keys.get_usable_by_secret(secret) is None
    other, other_secret = repos.api_keys.create_new("ok", expires_at=datetime.now(UTC) + timedelta(days=1))
    assert repos.api_keys.get_usable_by_secret(other_secret) is other
    assert [k.name for k in repos.api_keys.list_keys(active_only=True)] == ["tmp", "ok"]  # expiry != revoked


# ------------------------------------------------ relationship loading / N+1
def _count_queries(engine):
    statements: list[str] = []

    def before(conn, cursor, statement, *a):
        if statement != "BEGIN":  # SQLite engine emits explicit BEGINs; not interesting here
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return statements, lambda: event.remove(engine, "before_cursor_execute", before)


def test_get_with_relations_loads_everything_up_front(engine, session, seeded):
    domain, *_ = seeded
    domain_id = domain.id  # read before expiring (an expired attribute read would itself query)
    session.expire_all()
    stmts, stop = _count_queries(engine)
    loaded = DomainRepository(session).get_with_relations(domain_id)
    n_after_load = len(stmts)
    _ = (len(loaded.crawls), len(loaded.emails), len(loaded.people), len(loaded.email_patterns))
    stop()
    assert len(stmts) == n_after_load  # no lazy loads afterwards
    assert n_after_load == 5  # 1 domain + 4 selectin


def test_populate_existing_loads_relations_for_already_loaded_instance(session, seeded):
    domain, *_ = seeded
    again = DomainRepository(session).get_with_relations(domain.id)
    assert again is domain
    assert not {"crawls", "emails", "people", "email_patterns"} & inspect(again).unloaded


def test_email_get_full_and_crawl_get_with_pages(engine, session, seeded):
    _, crawl, _, email, person = seeded
    session.expire_all()
    full = EmailRepository(session).get_full(email.id)
    assert not {"evidence", "verifications", "people"} & inspect(full).unloaded
    assert len(full.evidence) == 1 and len(full.verifications) == 1 and full.people[0].id == person.id
    c = CrawlRepository(session).get_with_pages(crawl.id)
    assert "pages" not in inspect(c).unloaded and len(c.pages) == 1
    p = PersonRepository(session).get_full(person.id)
    assert not {"email", "evidence"} & inspect(p).unloaded


def test_list_for_domain_with_evidence_avoids_n_plus_one(engine, session, seeded):
    domain, *_ = seeded
    domain_id = domain.id
    repo = EmailRepository(session)
    for i in range(5):
        repo.get_or_create(domain_id, f"user{i}@example.com")
    session.commit()
    session.expire_all()
    stmts, stop = _count_queries(engine)
    emails = repo.list_for_domain(domain_id, with_evidence=True)
    for e in emails:
        _ = e.evidence
    stop()
    assert len(emails) == 6 and len(stmts) == 2


# ------------------------------------------------------------ cascade via repos
def test_deleting_domain_through_repository_cascades(repos, seeded):
    domain, *_ = seeded
    repos.domains.delete(domain)
    repos.session.commit()
    assert repos.emails.count() == 0 and repos.people.count() == 0 and repos.pages.count() == 0
    assert repos.crawls.count() == 0 and repos.verifications.count() == 0 and repos.patterns.count() == 0
