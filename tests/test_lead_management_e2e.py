"""Phase 11.5: one small end-to-end test of the lead management interface.

create/load leads -> filter -> sort -> paginate -> display/export (JSON, CSV) -> the stored data is unchanged.
Offline (the conftest blocks sockets); SQLite file; Persian text throughout.
"""

from __future__ import annotations

import csv
import io
import json

from typer.testing import CliRunner

from app.cli.main import app
from app.leads import LeadFilter, leads_to_csv, leads_to_json, select_lead_page
from tests.test_lead_listing import cli_database, file_digest, stored_state
from tests.test_lead_search import ACME, ALL, ARMAN, BARE, BOLT, CASPIAN

runner = CliRunner()


def run(*args):
    return runner.invoke(app, ["leads", *args])


def test_lead_management_end_to_end(monkeypatch, tmp_path):
    # 1. create/load: five leads stored in a database file, then read back through the CLI
    url = cli_database(monkeypatch, tmp_path, ALL)
    state, digest = stored_state(url), file_digest(url)
    assert len(state) == 5
    listed = run("list")
    assert listed.exit_code == 0, listed.output
    assert [line.split("\t")[1] for line in listed.stdout.splitlines()[1:]] == [l.domain for l in ALL]

    # 2. filter (Persian leads only) -> 3. sort (business name, descending) -> 4. paginate (1 per page)
    options = ("--language", "fa", "--sort-by", "business_name", "--desc", "--page-size", "1")
    first, second, third = (run("search", *options, "--page", str(n)) for n in (1, 2, 3))
    assert first.exit_code == second.exit_code == third.exit_code == 0
    assert "caspian.example" in first.stdout and "arman.example" not in first.stdout
    assert "arman.example" in second.stdout and "caspian.example" not in second.stdout
    assert "No leads on page 3" in third.stdout  # beyond the last page: a message, not an error

    # 5. export the same selection as JSON and CSV (UTF-8, Persian kept, same leads in the same order)
    as_json = run("export", "--format", "json", "--language", "fa", "--sort-by", "business_name", "--desc")
    as_csv = run("export", "--format", "csv", "--language", "fa", "--sort-by", "business_name", "--desc")
    page_two = run("export", "--format", "json", "--language", "fa", "--sort-by", "business_name", "--desc",
                   "--page-size", "1", "--page", "2")
    assert as_json.exit_code == as_csv.exit_code == page_two.exit_code == 0
    assert as_json.stdout_bytes == leads_to_json([CASPIAN, ARMAN], preserve_order=True).encode("utf-8")
    assert as_csv.stdout_bytes == leads_to_csv([CASPIAN, ARMAN], preserve_order=True).encode("utf-8")
    documents = json.loads(as_json.stdout_bytes.decode("utf-8"))
    assert [e["domain"] for e in documents["leads"]] == ["caspian.example", "arman.example"]
    assert documents["leads"][1]["business_name"] == "لوله‌کشی آرمان"  # Persian text and ZWNJ intact
    rows = list(csv.DictReader(io.StringIO(as_csv.stdout_bytes.decode("utf-8"), newline="")))
    assert [r["domain"] for r in rows] == ["caspian.example", "arman.example"]
    assert [e["domain"] for e in json.loads(page_two.stdout_bytes.decode("utf-8"))["leads"]] == ["arman.example"]
    out = tmp_path / "leads.csv"
    written = run("export", "--format", "csv", "--language", "fa", "--output", str(out), "--excel-bom")
    assert written.exit_code == 0 and out.read_bytes().startswith(b"\xef\xbb\xbf")

    # repeated queries are deterministic
    assert run("export", "--language", "fa", "--sort-by", "business_name", "--desc").stdout_bytes == as_json.stdout_bytes

    # 6. nothing was written to the database by any of the above
    assert stored_state(url) == state and file_digest(url) == digest


def test_export_paging_matches_the_query_and_bad_values_exit_2(monkeypatch, tmp_path):
    from app.db.session import session_scope
    from app.repositories import LeadRepository

    url = cli_database(monkeypatch, tmp_path, [ACME, ARMAN, BARE, BOLT, CASPIAN])
    before = stored_state(url)
    with session_scope() as session:
        selected = select_lead_page(LeadRepository(session), LeadFilter(), "domain", True, 2, 2).leads
    result = run("export", "--sort-by", "domain", "--desc", "--page", "2", "--page-size", "2")
    assert result.exit_code == 0
    assert result.stdout_bytes == leads_to_json(selected, preserve_order=True).encode("utf-8")
    assert [l.domain for l in selected] == ["bare.example", "arman.example"]  # caspian, bolt | bare, arman | acme
    for bad in (("--sort-by", "score"), ("--page", "0"), ("--page-size", "5000")):
        failed = run("export", *bad)
        assert failed.exit_code == 2 and "invalid paging" in failed.output
    assert stored_state(url) == before
