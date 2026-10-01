"""Phase 10.1: Lead JSON export (read-only, offline, deterministic)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.db import migrate
from app.leads import (
    BusinessLead, LeadDataError, export_stored_leads, lead_to_json, leads_to_json, write_leads_json,
)
from app.models import Lead
from app.repositories import LeadRepository
from tests.test_lead_persistence import T1, T2, acme, in_new_session, persian

FIELDS = list(BusinessLead(website="https://x.example/").to_dict())
MINIMAL = BusinessLead(website="https://minimal.example/")


@pytest.fixture
def repo(session):
    return LeadRepository(session)


def parse(text: str) -> dict:
    assert text.endswith("\n") and not text.endswith("\n\n")
    return json.loads(text)


# ---------------------------------------------------------------- 1. empty export
def test_empty_export_is_valid_json():
    assert parse(leads_to_json([])) == {"version": 1, "count": 0, "leads": []}


def test_empty_database(repo):
    text = export_stored_leads(repo)
    assert parse(text) == {"version": 1, "count": 0, "leads": []}
    assert text == leads_to_json([])


def test_empty_export_file_is_not_an_empty_file(tmp_path):
    path = write_leads_json(tmp_path / "leads.json", [])
    assert json.loads(path.read_bytes().decode("utf-8"))["leads"] == []


# ---------------------------------------------------------------- 2. single lead
def test_single_lead_uses_the_project_serialisation():
    doc = parse(leads_to_json([acme()]))
    assert doc["count"] == 1 and doc["leads"] == [acme().to_dict()]
    assert list(doc["leads"][0]) == FIELDS  # fixed field order
    assert BusinessLead.from_dict(doc["leads"][0]) == acme()  # reads back with the existing code
    assert parse(lead_to_json(acme())) == acme().to_dict()


def test_single_stored_lead(repo):
    repo.save(acme())
    doc = parse(export_stored_leads(repo))
    assert doc["leads"] == [acme().to_dict()]


# ---------------------------------------------------------------- 3. multiple leads
def test_multiple_leads_are_ordered_by_domain_not_by_input():
    a, p, m = acme(), persian(), MINIMAL
    forward = leads_to_json([a, p, m])
    assert leads_to_json([m, p, a]) == forward
    domains = [lead["domain"] for lead in parse(forward)["leads"]]
    assert domains == ["acme.example", "arman.example", "minimal.example"] and parse(forward)["count"] == 3


def test_multiple_stored_leads_do_not_depend_on_insertion_order(session, repo):
    for lead in (persian(), MINIMAL, acme()):
        repo.save(lead)
    stored = export_stored_leads(repo)
    assert stored == leads_to_json([acme(), persian(), MINIMAL])


def test_more_leads_than_one_read_batch(session, repo, monkeypatch):
    import app.leads.export as export

    monkeypatch.setattr(export, "_BATCH", 2)
    for n in range(5):
        repo.save(BusinessLead(website=f"https://site{n}.example/"))
    doc = parse(export_stored_leads(repo))
    assert [lead["domain"] for lead in doc["leads"]] == [f"site{n}.example" for n in range(5)] and doc["count"] == 5


def test_two_leads_with_one_identity_are_refused_not_dropped():
    with pytest.raises(LeadDataError):
        leads_to_json([acme(), acme(business_name="Other")])
    with pytest.raises(LeadDataError):
        leads_to_json([acme(), {"website": "https://acme.example/"}])  # type: ignore[list-item]


# ---------------------------------------------------------------- 4. Unicode / Persian
def test_persian_text_is_written_as_utf8_not_escaped(tmp_path):
    text = leads_to_json([persian()])
    assert "لوله‌کشی آرمان" in text and "\\u" not in text  # ZWNJ survives, nothing escaped
    path = write_leads_json(tmp_path / "fa.json", [persian()])
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    decoded = json.loads(raw.decode("utf-8"))
    assert decoded["leads"][0]["business_name"] == "لوله‌کشی آرمان"
    assert decoded["leads"][0]["phones"][0]["raw"] == "۰۲۱-۱۲۳۴۵۶۷۸"  # Persian digits kept
    assert BusinessLead.from_dict(decoded["leads"][0]) == persian()


def test_unicode_survives_the_database_round_trip(db_path):
    migrate.upgrade(db_path)
    in_new_session(db_path, lambda s, r: r.save(persian()))
    text = in_new_session(db_path, lambda s, r: export_stored_leads(r))
    assert parse(text)["leads"][0] == persian().to_dict()


@pytest.fixture
def db_path(tmp_path):
    return f"sqlite:///{tmp_path / 'export.db'}"


# ---------------------------------------------------------------- 5. optional fields
def test_optional_fields_are_null_or_empty_never_missing():
    entry = parse(leads_to_json([MINIMAL]))["leads"][0]
    assert list(entry) == FIELDS
    assert entry["business_name"] is None and entry["description"] is None and entry["address"] is None
    assert entry["emails"] == [] and entry["phones"] == [] and entry["social_profiles"] == []
    assert entry["page_type"] is None and entry["language"] is None and entry["source_url"] is None
    assert entry["first_seen"] is None and entry["last_seen"] is None
    assert entry["website"] == "https://minimal.example/" and entry["domain"] == "minimal.example"


def test_partly_filled_lead(repo):
    repo.save(BusinessLead(website="https://part.example/", emails=["a@part.example"], first_seen=T1, last_seen=T2,
                           page_type=PageCategory.CONTACT, address=Address(city="Tehran")))
    entry = parse(export_stored_leads(repo))["leads"][0]
    assert entry["emails"] == ["a@part.example"] and entry["address"]["city"] == "Tehran"
    assert entry["first_seen"] == T1.isoformat() and entry["last_seen"] == T2.isoformat()
    assert entry["business_name"] is None and entry["page_type"] == "contact"


# ---------------------------------------------------------------- 6. deterministic output
def test_output_is_byte_for_byte_stable(session, repo, tmp_path):
    for lead in (acme(), persian(), MINIMAL):
        repo.save(lead)
    first = export_stored_leads(repo)
    assert export_stored_leads(repo) == first
    a = write_leads_json(tmp_path / "a.json", repo.load_all())
    b = write_leads_json(tmp_path / "b.json", reversed(repo.load_all()))
    assert a.read_bytes() == b.read_bytes() == first.encode("utf-8")


def test_output_is_identical_across_processes():
    code = (
        "from app.leads import BusinessLead, leads_to_json;"
        "import sys;"
        "sys.stdout.buffer.write(leads_to_json([BusinessLead(website='https://b.example/', emails=['z@b.example','a@b.example']),"
        "BusinessLead(website='https://a.example/', business_name='لوله‌کشی')]).encode('utf-8'))"
    )
    outputs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, env={"PYTHONHASHSEED": seed, "PATH": ""}).stdout
        for seed in ("0", "1", "12345")
    }
    assert len(outputs) == 1
    assert json.loads(outputs.pop().decode("utf-8"))["count"] == 2


# ---------------------------------------------------------------- 7. export does not modify persistence
def test_export_does_not_modify_stored_data(session, repo):
    for lead in (acme(), persian()):
        repo.save(lead)
    session.commit()

    def snapshot():
        rows = session.execute(select(Lead).order_by(Lead.id)).scalars().all()
        return [(r.id, r.updated_at, r.created_at, r.crawl_id, repr(r.emails), r.website, r.first_seen, r.last_seen) for r in rows]

    before = snapshot()
    export_stored_leads(repo)
    export_stored_leads(repo)
    assert not session.new and not session.dirty and not session.deleted
    assert snapshot() == before
    assert (session.scalar(select(func.count()).select_from(Lead))) == 2


def test_export_does_not_modify_a_file_database(db_path, tmp_path):
    migrate.upgrade(db_path)
    in_new_session(db_path, lambda s, r: r.save(acme()))
    file = tmp_path / "export.db"
    before = file.read_bytes()
    in_new_session(db_path, lambda s, r: export_stored_leads(r))
    assert file.read_bytes() == before


def test_write_failure_leaves_no_partial_file(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(LeadDataError):
        write_leads_json(target, [acme(), acme()])
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]  # no temporary file left behind


def test_export_makes_no_network_requests(session, repo, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    repo.save(acme())
    assert parse(export_stored_leads(repo))["count"] == 1
