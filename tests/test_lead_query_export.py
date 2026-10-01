"""Phase 10.5: end-to-end Lead query and export (storage -> filter -> sort -> paginate -> JSON/CSV).

Offline and read-only. Tests marked "database" use the SQLite `session` fixture; the others use an in-memory stand-in that
implements the same `load_all(limit, offset)` read API, so the pipeline logic is also covered without a database.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import socket
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.leads import (
    BusinessLead, LeadDataError, LeadFilter, LeadFilterError, LeadPagingError, LeadQuery, export_query_csv, export_query_json,
    export_stored_leads, export_stored_leads_csv, filter_leads, leads_to_csv, leads_to_json, query_leads, query_stored_leads,
    query_to_csv, query_to_json, sort_leads, write_query_csv, write_query_json,
)
from app.leads.export_csv import COLUMNS
from app.models import Lead
from app.repositories import LeadRepository


def day(n: int) -> datetime:
    return datetime(2026, 3, n, 9, 0, tzinfo=UTC)


def make(domain, name=None, first=None, last=None, **fields) -> BusinessLead:
    return BusinessLead(website=f"https://{domain}/", business_name=name, first_seen=first, last_seen=last, **fields)


PHONE = [PhoneNumber("+982112345678", "۰۲۱-۱۲۳۴۵۶۷۸")]
LEADS = [
    make("acme.example", "Acme Plumbing", day(3), day(20), language="en", emails=["info@acme.example"], page_type=PageCategory.HOMEPAGE,
         address=Address(city="Springfield", country="US")),
    make("arman.example", "لوله‌کشی آرمان", day(1), day(25), language="fa", emails=["info@arman.example"], phones=PHONE,
         description="خدمات لوله‌کشی ساختمان، تهران", address=Address(city="تهران", country="ایران", street="خیابان ولیعصر"),
         social_profiles=[SocialLink("instagram", "https://www.instagram.com/arman")]),
    make("bolt.example", "Bolt, \"Fast\" Electric", day(5), day(12), language="en", emails=["hi@bolt.example"]),
    make("caspian.example", "کاسپین", day(2), day(25), language="fa", phones=PHONE, address=Address(city="رشت", country="ایران")),
    make("delta.example", "Delta Co", day(4), day(8), language="en", description="line one\nline two"),
    make("echo.example", None, None, None, language="fa"),
    make("farsi.example", "پارسا", day(6), day(9), language="fa", emails=["a@farsi.example"]),
]
FA = {"language": "fa"}


class FakeRepository:
    """Same read API as `LeadRepository.load_all`; records every call and refuses nothing else."""

    def __init__(self, leads):
        self.leads, self.calls = list(leads), 0

    def load_all(self, *, limit=100, offset=0):
        self.calls += 1
        return self.leads[offset:offset + limit]


def domains(leads) -> list[str]:
    return [lead.domain for lead in leads]


def csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


def json_leads(text: str) -> list[BusinessLead]:
    return [BusinessLead.from_dict(entry) for entry in json.loads(text)["leads"]]


QUERY = {"filter": FA, "sort_by": "last_seen", "descending": True, "page": 1, "page_size": 3}
# fa leads by last_seen desc: arman(25), caspian(25) [tie -> domain], farsi(9), echo(None, last)
EXPECTED_PAGE_1 = ["arman.example", "caspian.example", "farsi.example"]


# ---------------------------------------------------------------- pipeline
def test_pipeline_filters_sorts_and_pages_in_that_order():
    result = query_leads(LEADS, QUERY)
    assert domains(result.leads) == EXPECTED_PAGE_1
    assert (result.total, result.page, result.page_size, result.total_pages) == (4, 1, 3, 2)
    manual = sort_leads(filter_leads(LEADS, **FA), "last_seen", True)[:3]
    assert result.leads == tuple(manual)
    assert domains(query_leads(LEADS, QUERY | {"page": 2}).leads) == ["echo.example"]  # missing last_seen sorts last


def test_filtering_happens_before_paging_not_after():
    # page 1 of size 2 of all leads would hold 2 English/Persian mixes; filtered first it holds 2 matching leads
    assert domains(query_leads(LEADS, {"filter": FA, "page": 1, "page_size": 2}).leads) == ["arman.example", "caspian.example"]


def test_query_forms_are_equivalent():
    as_object = query_leads(LEADS, LeadQuery(filter=FA, sort_by="domain", page=2, page_size=2))
    as_mapping = query_leads(LEADS, {"filter": FA, "sort_by": "domain", "page": 2, "page_size": 2})
    as_keywords = query_leads(LEADS, filter=LeadFilter(language="fa"), sort_by="domain", page=2, page_size=2)
    assert as_object == as_mapping == as_keywords
    with pytest.raises(LeadDataError, match="not both"):
        query_leads(LEADS, LeadQuery(), page=1)


def test_no_query_selects_everything_sorted_by_domain_without_paging():
    result = query_leads(LEADS)
    assert domains(result.leads) == sorted(domains(LEADS))
    assert result.total == 7 and result.page is None and result.page_size is None and result.total_pages is None
    assert len(query_leads(LEADS, page_size=2).leads) == 2 and query_leads(LEADS, page_size=2).page == 1
    assert query_leads(LEADS, page=1).page_size == 50


# ---------------------------------------------------------------- exports contain only selected records
def test_exports_contain_only_the_selected_records_in_query_order():
    as_json, as_csv = query_to_json(LEADS, QUERY), query_to_csv(LEADS, QUERY)
    assert [lead.domain for lead in json_leads(as_json)] == EXPECTED_PAGE_1
    assert [row["domain"] for row in csv_rows(as_csv)] == EXPECTED_PAGE_1
    assert json.loads(as_json)["count"] == 3
    for unselected in ("acme.example", "bolt.example", "delta.example", "echo.example"):
        assert unselected not in as_json and unselected not in as_csv


def test_query_order_is_kept_but_plain_exports_still_use_domain_order():
    descending = {"sort_by": "domain", "descending": True}
    assert [row["domain"] for row in csv_rows(query_to_csv(LEADS, descending))] == sorted(domains(LEADS), reverse=True)
    assert [lead.domain for lead in json_leads(query_to_json(LEADS, descending))] == sorted(domains(LEADS), reverse=True)
    assert [lead.domain for lead in json_leads(leads_to_json(reversed(LEADS)))] == sorted(domains(LEADS))  # 10.1 unchanged
    assert [row["domain"] for row in csv_rows(leads_to_csv(reversed(LEADS)))] == sorted(domains(LEADS))  # 10.2 unchanged


def test_pages_are_disjoint_and_together_equal_the_unpaged_export():
    pages = [json_leads(query_to_json(LEADS, {"sort_by": "business_name", "page": n, "page_size": 3})) for n in (1, 2, 3, 4)]
    assert [len(p) for p in pages] == [3, 3, 1, 0]
    flat = [lead for page in pages for lead in page]
    assert flat == json_leads(query_to_json(LEADS, {"sort_by": "business_name"}))
    assert len(set(domains(flat))) == len(flat) == 7


def test_selected_leads_are_exported_unchanged():
    exported = {lead.domain: lead for lead in json_leads(query_to_json(LEADS, {}))}
    for lead in LEADS:
        assert exported[lead.domain] == lead


def test_page_beyond_result_and_empty_filter_export_valid_empty_documents():
    for query in ({"page": 99, "page_size": 5}, {"filter": {"language": "zz"}}):
        document = json.loads(query_to_json(LEADS, query))
        assert document == {"version": 1, "count": 0, "leads": []}
        assert list(csv.reader(io.StringIO(query_to_csv(LEADS, query), newline=""))) == [list(COLUMNS)]
    assert query_to_json([], {}) == leads_to_json([]) and query_to_csv([], {}) == leads_to_csv([])


# ---------------------------------------------------------------- JSON and CSV agree
@pytest.mark.parametrize("query", [
    {}, QUERY, {"filter": {"has_email": True}, "sort_by": "business_name"}, {"sort_by": "first_seen", "descending": True, "page": 2, "page_size": 2},
    {"filter": {"city": "تهران"}}, {"filter": {"page_type": "homepage"}},
])
def test_json_and_csv_hold_the_same_data(query):
    entries = json.loads(query_to_json(LEADS, query))["leads"]
    rows = csv_rows(query_to_csv(LEADS, query))
    assert len(entries) == len(rows)
    for entry, row in zip(entries, rows, strict=True):
        assert row["domain"] == entry["domain"] and row["website"] == entry["website"]
        assert row["business_name"] == (entry["business_name"] or "")
        assert row["description"] == (entry["description"] or "")
        assert row["emails"] == "|".join(entry["emails"])
        assert row["phones"] == "|".join(p["number"] for p in entry["phones"])
        assert row["social_profiles"] == "|".join(s["url"] for s in entry["social_profiles"])
        assert row["language"] == (entry["language"] or "") and row["page_type"] == (entry["page_type"] or "")
        assert row["first_seen"] == (entry["first_seen"] or "") and row["last_seen"] == (entry["last_seen"] or "")
        address = entry["address"] or {}
        assert row["address_city"] == (address.get("city") or "") and row["address_country"] == (address.get("country") or "")


# ---------------------------------------------------------------- determinism
def test_output_is_deterministic_and_independent_of_input_order():
    reference = (query_to_json(LEADS, QUERY), query_to_csv(LEADS, QUERY))
    assert (query_to_json(LEADS, QUERY), query_to_csv(LEADS, QUERY)) == reference
    for perm in itertools.islice(itertools.permutations(LEADS), 200):
        assert (query_to_json(perm, QUERY), query_to_csv(perm, QUERY)) == reference


def test_ties_are_broken_by_domain_in_exports():
    same = [make(f"{d}.example", "Same", day(1), day(2)) for d in ("delta", "alpha", "charlie", "bravo")]
    for descending in (False, True):
        rows = csv_rows(query_to_csv(same, {"sort_by": "business_name", "descending": descending}))
        assert [r["domain"] for r in rows] == ["alpha.example", "bravo.example", "charlie.example", "delta.example"]


# ---------------------------------------------------------------- Persian / Unicode
def test_persian_text_survives_both_formats_unescaped():
    as_json, as_csv = query_to_json(LEADS, {"filter": FA}), query_to_csv(LEADS, {"filter": FA})
    assert "لوله‌کشی آرمان" in as_json and "لوله‌کشی آرمان" in as_csv  # ZWNJ kept
    assert "\\u" not in as_json
    assert "۰۲۱-۱۲۳۴۵۶۷۸" not in as_csv  # CSV exports the normalised number, as in 10.2
    assert "+982112345678" in as_csv and "+982112345678" in as_json
    arman = next(row for row in csv_rows(as_csv) if row["domain"] == "arman.example")
    assert arman["business_name"] == "لوله‌کشی آرمان" and arman["address_city"] == "تهران" and arman["description"] == "خدمات لوله‌کشی ساختمان، تهران"
    assert next(l for l in json_leads(as_json) if l.domain == "arman.example") == LEADS[1]


def test_quotes_commas_and_newlines_round_trip_in_csv():
    rows = {r["domain"]: r for r in csv_rows(query_to_csv(LEADS, {}))}
    assert rows["bolt.example"]["business_name"] == 'Bolt, "Fast" Electric'
    assert rows["delta.example"]["description"] == "line one\nline two"


def test_written_files_are_utf8_and_match_the_text(tmp_path):
    repo = FakeRepository(LEADS)
    json_path = write_query_json(tmp_path / "out.json", repo, QUERY)
    csv_path = write_query_csv(tmp_path / "out.csv", repo, QUERY)
    bom_path = write_query_csv(tmp_path / "bom.csv", repo, QUERY, excel_bom=True)
    assert json_path.read_bytes() == export_query_json(repo, QUERY).encode("utf-8")
    assert csv_path.read_bytes() == export_query_csv(repo, QUERY).encode("utf-8")
    assert bom_path.read_bytes() == b"\xef\xbb\xbf" + csv_path.read_bytes()
    assert "لوله‌کشی آرمان" in json_path.read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bom.csv", "out.csv", "out.json"]  # no temporary files left


# ---------------------------------------------------------------- invalid queries
@pytest.mark.parametrize("bad, error", [
    ({"page": 0}, LeadPagingError), ({"page": "2"}, LeadPagingError), ({"page_size": 0}, LeadPagingError),
    ({"page_size": 1001}, LeadPagingError), ({"page_size": True}, LeadPagingError), ({"sort_by": "score"}, LeadPagingError),
    ({"descending": "yes"}, LeadPagingError), ({"filter": {"colour": "red"}}, LeadFilterError),
    ({"filter": {"has_email": "yes"}}, LeadFilterError), ({"filter": 5}, LeadFilterError), ({"nonsense": 1}, LeadDataError),
])
def test_invalid_queries_fail_clearly_before_anything_is_read(bad, error, tmp_path):
    repo = FakeRepository(LEADS)
    with pytest.raises(error):
        query_stored_leads(repo, bad)
    with pytest.raises(error):
        export_query_json(repo, bad)
    with pytest.raises(error):
        write_query_csv(tmp_path / "never.csv", repo, bad)
    assert repo.calls == 0 and list(tmp_path.iterdir()) == []


def test_failed_export_leaves_an_existing_file_untouched(tmp_path):
    target = tmp_path / "keep.json"
    target.write_text("previous", encoding="utf-8")
    with pytest.raises(LeadDataError):
        write_query_json(target, FakeRepository(LEADS), {"page": -1})
    assert target.read_text(encoding="utf-8") == "previous"
    duplicates = FakeRepository([make("same.example", "A"), BusinessLead(website="https://www.same.example/", business_name="B")])
    with pytest.raises(LeadDataError, match="same identity"):
        write_query_json(target, duplicates, {})
    assert target.read_text(encoding="utf-8") == "previous"


# ---------------------------------------------------------------- no network, read-only
def test_no_network_is_used(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    repo = FakeRepository(LEADS)
    assert domains(query_stored_leads(repo, QUERY).leads) == EXPECTED_PAGE_1
    write_query_json(tmp_path / "a.json", repo, QUERY)
    write_query_csv(tmp_path / "a.csv", repo, QUERY)


def test_in_memory_leads_are_not_modified():
    before = [lead.to_dict() for lead in LEADS]
    snapshot = list(LEADS)
    query_to_json(LEADS, QUERY), query_to_csv(LEADS, QUERY)
    assert LEADS == snapshot and [lead.to_dict() for lead in LEADS] == before


# ---------------------------------------------------------------- database
@pytest.fixture
def repo(session):
    return LeadRepository(session)


@pytest.fixture
def stored(session, repo):
    for lead in LEADS:
        repo.save(lead)
    session.commit()
    return repo


def snapshot(session):
    return [
        (r.id, r.updated_at, r.created_at, r.business_name, r.website, r.emails, r.phones, r.language, r.first_seen, r.last_seen)
        for r in session.scalars(select(Lead).order_by(Lead.id))
    ]


def test_database_pipeline_matches_in_memory_pipeline(stored):
    assert query_stored_leads(stored, QUERY).leads == query_leads(LEADS, QUERY).leads
    assert domains(query_stored_leads(stored, QUERY).leads) == EXPECTED_PAGE_1
    assert export_query_json(stored, QUERY) == query_to_json(LEADS, QUERY)
    assert export_query_csv(stored, QUERY) == query_to_csv(LEADS, QUERY)


def test_database_exports_contain_only_selected_leads_and_agree(stored):
    entries = json.loads(export_query_json(stored, QUERY))["leads"]
    rows = csv_rows(export_query_csv(stored, QUERY))
    assert [e["domain"] for e in entries] == [r["domain"] for r in rows] == EXPECTED_PAGE_1
    assert next(e for e in entries if e["domain"] == "arman.example")["business_name"] == "لوله‌کشی آرمان"


def test_database_without_query_equals_the_plain_exports_in_domain_order(stored):
    assert export_query_json(stored, {}) == export_stored_leads(stored)
    assert export_query_csv(stored, {}) == export_stored_leads_csv(stored)


def test_database_empty_and_beyond_page(session, repo):
    assert query_stored_leads(repo, QUERY).leads == () and query_stored_leads(repo, QUERY).total == 0
    assert json.loads(export_query_json(repo, {}))["count"] == 0
    for lead in LEADS:
        repo.save(lead)
    assert query_stored_leads(repo, {"page": 50, "page_size": 10}).leads == ()


def test_database_stored_leads_remain_unchanged_by_queries_and_exports(session, stored, tmp_path):
    before = snapshot(session)
    for query in ({}, QUERY, {"filter": {"language": "zz"}}, {"page": 9}):
        query_stored_leads(stored, query)
        export_query_json(stored, query)
        export_query_csv(stored, query)
    write_query_json(tmp_path / "q.json", stored, QUERY)
    write_query_csv(tmp_path / "q.csv", stored, QUERY)
    assert not session.new and not session.dirty and not session.deleted
    session.rollback()
    assert snapshot(session) == before


def test_database_more_leads_than_one_read_batch(session, repo):
    many = [make(f"site{n:04d}.example", f"Site {n:04d}", day(1 + n % 20), day(21), language="fa" if n % 2 else "en") for n in range(1203)]
    for lead in many:
        repo.save(lead)
    session.commit()
    result = query_stored_leads(repo, {"filter": {"language": "fa"}, "sort_by": "domain", "descending": True, "page": 3, "page_size": 100})
    assert result.total == 601
    assert domains(result.leads) == [f"site{n:04d}.example" for n in range(801, 601, -2)]  # odd numbers, 201st-300th of 601
    document = json.loads(export_query_json(repo, {"page": 13, "page_size": 100}))
    assert document["count"] == 3 and [e["domain"] for e in document["leads"]] == ["site1200.example", "site1201.example", "site1202.example"]
