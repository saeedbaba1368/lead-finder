"""Phase 10.2: Lead CSV export (read-only, offline, deterministic)."""

from __future__ import annotations

import csv
import io
import subprocess
import sys

import pytest
from sqlalchemy import select

from app.crawler import PageCategory, PhoneNumber, SocialLink
from app.crawler.structured import Address
from app.leads import (
    BusinessLead, LeadDataError, export_stored_leads, export_stored_leads_csv, leads_to_csv, leads_to_json, write_leads_csv,
)
from app.leads.export_csv import COLUMNS
from app.models import Lead
from app.repositories import LeadRepository
from tests.test_lead_persistence import T1, T2, acme, persian

EXPECTED_COLUMNS = [
    "business_name", "website", "domain", "description", "emails", "phones",
    "address_street", "address_city", "address_region", "address_postal_code", "address_country", "address_formatted",
    "social_profiles", "page_type", "language", "source_url", "first_seen", "last_seen",
]
MINIMAL = BusinessLead(website="https://minimal.example/")


def read(text: str) -> list[list[str]]:
    """Parse CSV text the way any RFC 4180 reader does."""
    return list(csv.reader(io.StringIO(text, newline="")))


def records(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


@pytest.fixture
def repo(session):
    return LeadRepository(session)


# ---------------------------------------------------------------- 1. empty export
def test_empty_export_is_just_the_header():
    text = leads_to_csv([])
    assert text == ",".join(EXPECTED_COLUMNS) + "\r\n"
    assert read(text) == [EXPECTED_COLUMNS]


def test_empty_database(repo):
    assert export_stored_leads_csv(repo) == leads_to_csv([])


def test_empty_export_file(tmp_path):
    path = write_leads_csv(tmp_path / "leads.csv", [])
    assert path.read_bytes() == (",".join(EXPECTED_COLUMNS) + "\r\n").encode("utf-8")


# ---------------------------------------------------------------- 2. single lead
def test_single_lead():
    rows = records(leads_to_csv([acme()]))
    assert len(rows) == 1
    row = rows[0]
    assert row["business_name"] == "Acme Plumbing" and row["website"] == "https://acme.example/"
    assert row["domain"] == "acme.example" and row["description"] == "Plumbers in Springfield"
    assert row["emails"] == "info@acme.example|sales@acme.example"
    assert row["phones"] == "+14155550199"
    assert (row["address_street"], row["address_city"], row["address_country"]) == ("1 Main St", "Springfield", "US")
    assert row["address_region"] == "" and row["address_formatted"] == "1 Main St, Springfield, US"
    assert row["social_profiles"] == "https://www.linkedin.com/company/acme|https://x.com/acme"
    assert (row["page_type"], row["language"], row["source_url"]) == ("homepage", "en", "https://acme.example/")
    assert (row["first_seen"], row["last_seen"]) == (T1.isoformat(), T2.isoformat())


def test_single_stored_lead(repo):
    repo.save(acme())
    assert export_stored_leads_csv(repo) == leads_to_csv([acme()])


# ---------------------------------------------------------------- 3. multiple leads
def test_multiple_leads_ordered_by_domain_whatever_the_input():
    forward = leads_to_csv([acme(), persian(), MINIMAL])
    assert leads_to_csv([MINIMAL, persian(), acme()]) == forward
    assert [r["domain"] for r in records(forward)] == ["acme.example", "arman.example", "minimal.example"]
    assert all(len(row) == len(COLUMNS) for row in read(forward))


def test_stored_leads_do_not_depend_on_insertion_order(repo):
    for lead in (persian(), MINIMAL, acme()):
        repo.save(lead)
    assert export_stored_leads_csv(repo) == leads_to_csv([acme(), persian(), MINIMAL])


def test_more_leads_than_one_read_batch(repo, monkeypatch):
    import app.leads.export_csv as module

    monkeypatch.setattr(module, "_BATCH", 2)
    for n in range(5):
        repo.save(BusinessLead(website=f"https://site{n}.example/"))
    assert [r["domain"] for r in records(export_stored_leads_csv(repo))] == [f"site{n}.example" for n in range(5)]


def test_duplicate_identities_and_non_leads_are_refused():
    with pytest.raises(LeadDataError):
        leads_to_csv([acme(), acme(business_name="Other")])
    with pytest.raises(LeadDataError):
        leads_to_csv([acme(), "nope"])  # type: ignore[list-item]


# ---------------------------------------------------------------- 4. Persian text
def test_persian_text(tmp_path):
    text = leads_to_csv([persian()])
    assert "لوله‌کشی آرمان" in text and "\\u" not in text  # ZWNJ kept, nothing escaped
    path = write_leads_csv(tmp_path / "fa.csv", [persian()])
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    row = records(raw.decode("utf-8"))[0]
    assert row["business_name"] == "لوله‌کشی آرمان" and row["description"] == "خدمات لوله‌کشی ساختمان در تهران"
    assert (row["address_city"], row["address_country"]) == ("تهران", "ایران")
    assert row["address_formatted"] == "خیابان ولیعصر، تهران، ایران"  # Persian comma is not a CSV comma
    assert row["phones"] == "+982112345678"


def test_excel_bom_is_opt_in_and_readable(tmp_path):
    path = write_leads_csv(tmp_path / "bom.csv", [persian()], excel_bom=True)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert records(raw.decode("utf-8-sig"))[0]["business_name"] == "لوله‌کشی آرمان"


# ---------------------------------------------------------------- 5. commas / 6. quotes / 7. newlines
def special(**fields) -> BusinessLead:
    return BusinessLead(website="https://special.example/", **fields)


def test_commas_are_quoted():
    lead = special(business_name="Smith, Jones & Sons", description="Pipes, boilers, taps",
                   address=Address(street="1 Main St, Suite 4", formatted="1 Main St, Suite 4, Springfield"))
    text = leads_to_csv([lead])
    assert '"Smith, Jones & Sons"' in text and '"Pipes, boilers, taps"' in text
    row = records(text)[0]
    assert row["business_name"] == "Smith, Jones & Sons" and row["address_street"] == "1 Main St, Suite 4"
    assert len(read(text)[1]) == len(COLUMNS)


def test_quotes_are_doubled():
    lead = special(business_name='The "Best" Plumber', description='He said "hello", then left')
    text = leads_to_csv([lead])
    assert '"The ""Best"" Plumber"' in text and '"He said ""hello"", then left"' in text
    row = records(text)[0]
    assert row["business_name"] == 'The "Best" Plumber' and row["description"] == 'He said "hello", then left'


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_newlines_stay_inside_the_field(newline):
    lead = special(description=f"Line one{newline}Line two{newline}Line three", address=Address(street=f"1 Main St{newline}Floor 2"))
    text = leads_to_csv([lead])
    rows = read(text)
    assert len(rows) == 2  # header + ONE record, although the text has several lines
    row = records(text)[0]
    assert row["description"] == f"Line one{newline}Line two{newline}Line three"
    assert row["address_street"] == f"1 Main St{newline}Floor 2"
    assert len(rows[1]) == len(COLUMNS)


def test_everything_special_in_one_field():
    value = 'a, "b"\nc, "d"'
    row = records(leads_to_csv([special(description=value)]))[0]
    assert row["description"] == value


def test_a_value_with_the_list_separator_is_refused_not_made_ambiguous():
    # emails/phones/URLs cannot contain it; a social URL built by hand is the only way in, and it must not be exported ambiguously
    lead = special(social_profiles=[SocialLink("x", "https://x.com/a|b")])
    with pytest.raises(LeadDataError):
        leads_to_csv([lead])


# ---------------------------------------------------------------- 8. empty fields
def test_empty_values_are_empty_cells():
    row = records(leads_to_csv([MINIMAL]))[0]
    assert row["website"] == "https://minimal.example/" and row["domain"] == "minimal.example"
    assert {k: v for k, v in row.items() if k not in ("website", "domain")} == {
        k: "" for k in EXPECTED_COLUMNS if k not in ("website", "domain")
    }
    assert "None" not in leads_to_csv([MINIMAL]) and "null" not in leads_to_csv([MINIMAL])


def test_partly_filled_lead_and_a_partial_address():
    lead = special(emails=["a@special.example"], address=Address(city="Tehran"), page_type=PageCategory.CONTACT)
    row = records(leads_to_csv([lead]))[0]
    assert row["emails"] == "a@special.example" and row["address_city"] == "Tehran" and row["address_street"] == ""
    assert row["page_type"] == "contact" and row["business_name"] == "" and row["first_seen"] == ""


# ---------------------------------------------------------------- 9. deterministic columns
def test_columns_are_fixed_and_cover_every_lead_field():
    assert list(COLUMNS) == EXPECTED_COLUMNS
    for lead in (MINIMAL, acme(), persian()):
        assert read(leads_to_csv([lead]))[0] == EXPECTED_COLUMNS
    # every BusinessLead field is exported (address as its parts, social_profiles / phones as values)
    exported = {c.removeprefix("address_") for c in COLUMNS}
    assert set(MINIMAL.to_dict()) - {"address"} <= set(COLUMNS) and {"street", "city", "formatted"} <= exported


def test_output_is_byte_stable(repo, tmp_path):
    for lead in (acme(), persian(), MINIMAL):
        repo.save(lead)
    first = export_stored_leads_csv(repo)
    assert export_stored_leads_csv(repo) == first
    a = write_leads_csv(tmp_path / "a.csv", repo.load_all())
    b = write_leads_csv(tmp_path / "b.csv", reversed(repo.load_all()))
    assert a.read_bytes() == b.read_bytes() == first.encode("utf-8")
    assert b"\r\n" in a.read_bytes() and b"\r\r" not in a.read_bytes()  # no newline translation


def test_output_is_identical_across_processes():
    code = (
        "import sys;from app.leads import BusinessLead, leads_to_csv;"
        "sys.stdout.buffer.write(leads_to_csv([BusinessLead(website='https://b.example/', emails=['z@b.example','a@b.example']),"
        "BusinessLead(website='https://a.example/', business_name='لوله‌کشی, \"x\"')]).encode('utf-8'))"
    )
    outputs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, env={"PYTHONHASHSEED": seed, "PATH": ""}).stdout
        for seed in ("0", "7", "4242")
    }
    assert len(outputs) == 1


# ---------------------------------------------------------------- read-only, JSON untouched
def test_export_does_not_modify_persistence(session, repo):
    repo.save(acme())
    repo.save(persian())
    session.commit()

    def snapshot():
        return [(r.id, r.updated_at, r.emails, r.website) for r in session.scalars(select(Lead).order_by(Lead.id))]

    before = snapshot()
    export_stored_leads_csv(repo)
    assert not session.new and not session.dirty and not session.deleted
    assert snapshot() == before


def test_json_export_is_unchanged_by_the_csv_export(repo):
    repo.save(acme())
    json_before = export_stored_leads(repo)
    export_stored_leads_csv(repo)
    assert export_stored_leads(repo) == json_before == leads_to_json([acme()])


def test_failed_write_leaves_the_old_file_and_no_temporary_file(tmp_path):
    target = tmp_path / "out.csv"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(LeadDataError):
        write_leads_csv(target, [acme(), acme()])
    assert target.read_text(encoding="utf-8") == "old" and [p.name for p in tmp_path.iterdir()] == ["out.csv"]
