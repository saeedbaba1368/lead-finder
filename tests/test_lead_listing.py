"""Phase 11.1: lead listing (`leadfinder leads list`). Read-only and offline (the conftest blocks sockets).

Pure tests use an in-memory stand-in with the `load_all(limit, offset)` read API; database and CLI tests use SQLite files,
like `test_cli_crawl_state`.
"""

from __future__ import annotations

import hashlib
import itertools
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.main import app
from app.core.config import get_settings
from app.crawler import PhoneNumber
from app.db.base import Base
from app.db.session import create_db_engine, get_engine
from app.leads import BusinessLead, LeadRow, format_lead_listing, list_stored_leads
from app.leads.listing import COLUMNS, EMPTY_MESSAGE
from app.models import Lead
from app.repositories import LeadRepository

runner = CliRunner()
T1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 2, 6, 11, 30, tzinfo=UTC)
HEADER = "\t".join(COLUMNS)

ACME = BusinessLead(
    website="https://acme.example/", business_name="Acme Plumbing", emails=["info@acme.example", "sales@acme.example"],
    phones=[PhoneNumber("+14155550199", "+1 415 555 0199")], first_seen=T1, last_seen=T2,
)
ARMAN = BusinessLead(
    website="https://arman.example/", business_name="لوله‌کشی آرمان", emails=["info@arman.example"],
    phones=[PhoneNumber("+982112345678", "۰۲۱-۱۲۳۴۵۶۷۸")], first_seen=T1, last_seen=T1,
)
BARE = BusinessLead(website="https://bare.example/")
ACME_LINE = "Acme Plumbing\tacme.example\thttps://acme.example/\t2\t1\t2026-01-05T10:00:00+00:00\t2026-02-06T11:30:00+00:00"
ARMAN_LINE = "لوله‌کشی آرمان\tarman.example\thttps://arman.example/\t1\t1\t2026-01-05T10:00:00+00:00\t2026-01-05T10:00:00+00:00"
BARE_LINE = "-\tbare.example\thttps://bare.example/\t0\t0\t-\t-"


class FakeRepository:
    def __init__(self, leads):
        self.leads = list(leads)

    def load_all(self, *, limit=100, offset=0):
        return self.leads[offset:offset + limit]


def listing(leads) -> str:
    return format_lead_listing(list_stored_leads(FakeRepository(leads)))


def cli_database(monkeypatch, tmp_path, leads=(), *, create_tables=True) -> str:
    """A SQLite file (own engine, then disposed) holding `leads`, selected through APP_DATABASE_URL."""
    url = f"sqlite:///{tmp_path / 'leads.db'}"
    engine = create_db_engine(url)
    if create_tables:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            repo = LeadRepository(session)
            for lead in leads:
                repo.save(lead)
            session.commit()
    engine.dispose()
    monkeypatch.setenv("APP_DATABASE_URL", url)
    get_settings.cache_clear()
    get_engine.cache_clear()
    return url


def stored_state(url: str):
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            return [
                (r.id, r.created_at, r.updated_at, r.business_name, r.website, r.emails, r.phones, r.first_seen, r.last_seen)
                for r in session.scalars(select(Lead).order_by(Lead.id))
            ]
    finally:
        engine.dispose()


def file_digest(url: str) -> str:
    return hashlib.sha256(open(url.removeprefix("sqlite:///"), "rb").read()).hexdigest()


# ---------------------------------------------------------------- 1. empty database
def test_empty_listing():
    assert list_stored_leads(FakeRepository([])) == []
    assert listing([]) == EMPTY_MESSAGE == "No leads found."


def test_cli_empty_database(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path)
    result = runner.invoke(app, ["leads", "list"])
    assert result.exit_code == 0, result.output
    assert result.output == "No leads found.\n"


def test_cli_database_without_leads_table_is_a_clear_error(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, create_tables=False)
    result = runner.invoke(app, ["leads", "list"])
    assert result.exit_code == 1
    assert "no leads table" in result.output and "db upgrade" in result.output


# ---------------------------------------------------------------- 2. one lead
def test_one_lead_rows_and_text():
    rows = list_stored_leads(FakeRepository([ACME]))
    assert rows == [LeadRow("Acme Plumbing", "acme.example", "https://acme.example/", 2, 1, T1, T2)]
    assert format_lead_listing(rows) == f"{HEADER}\n{ACME_LINE}"


def test_cli_one_lead(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, [ACME])
    result = runner.invoke(app, ["leads", "list"])
    assert result.exit_code == 0, result.output
    assert result.output == f"{HEADER}\n{ACME_LINE}\n"


def test_missing_values_are_shown_as_dashes():
    assert listing([BARE]) == f"{HEADER}\n{BARE_LINE}"


# ---------------------------------------------------------------- 3. multiple leads
def test_multiple_leads_are_listed_in_domain_order_whatever_the_stored_order():
    expected = f"{HEADER}\n{ACME_LINE}\n{ARMAN_LINE}\n{BARE_LINE}"
    for order in itertools.permutations([BARE, ACME, ARMAN]):
        assert listing(order) == expected


def test_cli_multiple_leads(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, [BARE, ARMAN, ACME])
    result = runner.invoke(app, ["leads", "list"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [HEADER, ACME_LINE, ARMAN_LINE, BARE_LINE]


def test_more_leads_than_one_read_batch_are_all_listed():
    many = [BusinessLead(website=f"https://site{n:04d}.example/") for n in range(1203)]
    lines = listing(reversed(many)).splitlines()
    assert len(lines) == 1204 and lines[1].split("\t")[1] == "site0000.example" and lines[-1].split("\t")[1] == "site1202.example"


# ---------------------------------------------------------------- 4. Unicode / Persian
def test_persian_lead_is_shown_exactly():
    text = listing([ARMAN])
    assert "لوله‌کشی آرمان" in text and "\u200c" in text  # ZWNJ kept
    assert text.splitlines()[1].split("\t")[0] == "لوله‌کشی آرمان"


def test_cli_persian_lead(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, [ARMAN])
    result = runner.invoke(app, ["leads", "list"])
    assert result.exit_code == 0, result.output
    assert result.output == f"{HEADER}\n{ARMAN_LINE}\n"
    assert "لوله‌کشی آرمان" in result.output and "\\u" not in result.output


def test_unsafe_text_cannot_break_the_layout():
    nasty = BusinessLead(website="https://nasty.example/", business_name="Evil\tName\nwith\r\nlines \x1b[31mred\x07")
    lines = listing([nasty]).splitlines()
    assert len(lines) == 2  # header + exactly one row
    cells = lines[1].split("\t")
    assert len(cells) == len(COLUMNS) and cells[0] == "Evil Name with lines [31mred"
    assert "\x1b" not in lines[1] and "\x07" not in lines[1]


# ---------------------------------------------------------------- 5. deterministic output
def test_output_is_deterministic():
    first = listing([ACME, ARMAN, BARE])
    assert all(listing([ACME, ARMAN, BARE]) == first for _ in range(5))
    assert listing([BARE, ARMAN, ACME]) == first


def test_cli_output_is_deterministic(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, [ACME, ARMAN, BARE])
    outputs = {runner.invoke(app, ["leads", "list"]).output for _ in range(3)}
    assert len(outputs) == 1 and outputs.pop().startswith(HEADER)


# ---------------------------------------------------------------- 6. database remains unchanged
def test_cli_listing_does_not_change_the_database(monkeypatch, tmp_path):
    url = cli_database(monkeypatch, tmp_path, [ACME, ARMAN, BARE])
    state, digest = stored_state(url), file_digest(url)
    for _ in range(2):
        assert runner.invoke(app, ["leads", "list"]).exit_code == 0
    assert stored_state(url) == state and len(state) == 3
    assert file_digest(url) == digest  # not even a byte of the database file moved


def test_empty_database_stays_empty_and_unchanged(monkeypatch, tmp_path):
    url = cli_database(monkeypatch, tmp_path)
    digest = file_digest(url)
    assert runner.invoke(app, ["leads", "list"]).exit_code == 0
    assert stored_state(url) == [] and file_digest(url) == digest


def test_listing_through_a_session_does_not_write(session):
    repo = LeadRepository(session)
    repo.save(ACME)
    repo.save(ARMAN)
    session.commit()
    before = [(r.id, r.updated_at, r.emails) for r in session.scalars(select(Lead).order_by(Lead.id))]
    assert format_lead_listing(list_stored_leads(repo)) == f"{HEADER}\n{ACME_LINE}\n{ARMAN_LINE}"
    assert not session.new and not session.dirty and not session.deleted
    assert [(r.id, r.updated_at, r.emails) for r in session.scalars(select(Lead).order_by(Lead.id))] == before


# ---------------------------------------------------------------- CLI wiring
def test_leads_command_group_is_registered_and_has_no_extra_options():
    assert runner.invoke(app, ["leads", "--help"]).exit_code == 0
    helptext = runner.invoke(app, ["leads", "list", "--help"]).output
    assert runner.invoke(app, ["leads", "list", "--help"]).exit_code == 0
    for option in ("--filter", "--limit", "--language", "--score"):  # 11.4 added --sort-by/--page/--page-size
        assert option not in helptext
    assert "leads" in runner.invoke(app, ["--help"]).output
