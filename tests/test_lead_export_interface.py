"""Phase 11.3: lead export interface (`leadfinder leads export`) on top of the Phase 10 exports. Read-only and offline
(the conftest blocks sockets). Pure tests use an in-memory stand-in with the `load_all` read API; CLI tests use SQLite
files. CLI output is compared as bytes (`stdout_bytes`) because the exports are UTF-8 with CRLF in CSV."""

from __future__ import annotations

import csv
import io
import json

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.leads import (
    BusinessLead, LeadExportError, LeadFilter, LeadFilterError, export_query_csv, export_query_json, leads_to_csv, leads_to_json,
    render_export, select_leads, write_export,
)
from app.leads.export_csv import COLUMNS
from tests.test_lead_listing import FakeRepository, cli_database, file_digest, stored_state
from tests.test_lead_search import ACME, ALL, ARMAN, BARE, BOLT, CASPIAN

runner = CliRunner()
NASTY = BusinessLead(website="https://nasty.example/", business_name='Say "hi", friend', description="line one\nline two, \"quoted\"",
                     emails=["a@nasty.example"], language="en")


def export(*args):
    return runner.invoke(app, ["leads", "export", *args])


def domains_in_csv(data: bytes) -> list[str]:
    return [row["domain"] for row in csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))]


def domains_in_json(data: bytes) -> list[str]:
    return [entry["domain"] for entry in json.loads(data.decode("utf-8"))["leads"]]


# ---------------------------------------------------------------- 1. JSON export
def test_render_json_is_the_phase_10_document():
    assert render_export(ALL, "json") == leads_to_json(ALL).encode("utf-8")
    document = json.loads(render_export(ALL, "json").decode("utf-8"))
    assert document["version"] == 1 and document["count"] == 5
    assert [BusinessLead.from_dict(e) for e in document["leads"]] == ALL


def test_cli_json_export(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    result = export("--format", "json")
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == leads_to_json(ALL).encode("utf-8")
    assert export().stdout_bytes == result.stdout_bytes  # json is the default
    assert export("-f", "JSON").stdout_bytes == result.stdout_bytes  # case-insensitive


# ---------------------------------------------------------------- 2. CSV export
def test_render_csv_is_the_phase_10_csv():
    data = render_export(ALL, "csv")
    assert data == leads_to_csv(ALL).encode("utf-8") and data.count(b"\r\n") == 6  # header + 5 rows
    assert next(csv.reader(io.StringIO(data.decode("utf-8"), newline=""))) == list(COLUMNS)


def test_cli_csv_export(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    result = export("--format", "csv")
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == leads_to_csv(ALL).encode("utf-8")
    assert domains_in_csv(result.stdout_bytes) == [lead.domain for lead in ALL]


# ---------------------------------------------------------------- 3. filtered export
def test_selection_is_the_phase_10_query():
    flt = LeadFilter(language="fa")
    selected = select_leads(FakeRepository(ALL), flt)
    assert selected == (ARMAN, CASPIAN)
    assert render_export(selected, "json") == export_query_json(FakeRepository(ALL), {"filter": flt}).encode("utf-8")
    assert render_export(selected, "csv") == export_query_csv(FakeRepository(ALL), {"filter": flt}).encode("utf-8")
    assert select_leads(FakeRepository(ALL)) == tuple(ALL)


def test_cli_filtered_export(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    as_csv = export("--format", "csv", "--language", "fa")
    assert as_csv.exit_code == 0, as_csv.output
    assert as_csv.stdout_bytes == leads_to_csv([ARMAN, CASPIAN]).encode("utf-8")
    as_json = export("--format", "json", "--has-email", "--language", "en", "--language", "fa")
    assert domains_in_json(as_json.stdout_bytes) == ["acme.example", "bolt.example", "caspian.example"]
    assert json.loads(as_json.stdout_bytes)["count"] == 3
    assert domains_in_csv(export("-f", "csv", "--no-phone", "--no-email").stdout_bytes) == ["bare.example"]
    assert domains_in_json(export("--page-type", "homepage", "--city", "رشت").stdout_bytes) == ["caspian.example"]
    for unselected in ("acme.example", "bolt.example", "bare.example"):
        assert unselected not in as_csv.stdout_bytes.decode("utf-8")


# ---------------------------------------------------------------- 4. empty export
def test_render_empty_exports_are_valid():
    assert json.loads(render_export([], "json")) == {"version": 1, "count": 0, "leads": []}
    assert list(csv.reader(io.StringIO(render_export([], "csv").decode("utf-8"), newline=""))) == [list(COLUMNS)]
    assert select_leads(FakeRepository([]), LeadFilter(language="en")) == ()


def test_cli_empty_database_and_empty_selection(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, [])
    for fmt in ("json", "csv"):
        result = export("--format", fmt)
        assert result.exit_code == 0 and result.stdout_bytes == render_export([], fmt)


def test_cli_no_match_is_a_valid_empty_export(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    none_json, none_csv = export("--language", "zz"), export("--format", "csv", "--language", "zz")
    assert none_json.exit_code == 0 and json.loads(none_json.stdout_bytes) == {"version": 1, "count": 0, "leads": []}
    assert none_csv.exit_code == 0 and none_csv.stdout_bytes == render_export([], "csv")
    target = tmp_path / "empty.csv"
    written = export("--format", "csv", "--language", "zz", "--output", str(target))
    assert written.exit_code == 0 and "wrote 0 lead(s)" in written.output
    assert target.read_bytes() == render_export([], "csv")


# ---------------------------------------------------------------- 5. Persian data and CSV escaping
def test_persian_text_is_utf8_unescaped_in_both_formats():
    as_json, as_csv = render_export([ARMAN], "json"), render_export([ARMAN], "csv")
    for data in (as_json, as_csv):
        text = data.decode("utf-8")
        assert "لوله‌کشی آرمان" in text and "\u200c" in text and "\\u" not in text
        assert "لوله‌کشی آرمان".encode("utf-8") in data
    assert not as_json.startswith(b"\xef\xbb\xbf") and not as_csv.startswith(b"\xef\xbb\xbf")  # no BOM by default
    row = next(csv.DictReader(io.StringIO(as_csv.decode("utf-8"), newline="")))
    assert row["business_name"] == "لوله‌کشی آرمان" and row["address_city"] == "تهران" and row["address_country"] == "ایران"


def test_correct_csv_escaping_round_trips():
    data = render_export([NASTY], "csv")
    text = data.decode("utf-8")
    assert '"Say ""hi"", friend"' in text  # quoted field, doubled quotes
    row = next(csv.DictReader(io.StringIO(text, newline="")))
    assert row["business_name"] == 'Say "hi", friend' and row["description"] == 'line one\nline two, "quoted"'
    assert BusinessLead.from_dict(json.loads(render_export([NASTY], "json"))["leads"][0]) == NASTY


def test_excel_bom_is_csv_only():
    plain = render_export([ARMAN], "csv")
    assert render_export([ARMAN], "csv", excel_bom=True) == b"\xef\xbb\xbf" + plain
    with pytest.raises(LeadExportError, match="CSV only"):
        render_export([ARMAN], "json", excel_bom=True)


def test_cli_persian_export_and_bom(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL + [NASTY])
    as_json = export("--format", "json", "--city", "تهران")
    assert as_json.stdout_bytes == leads_to_json([ARMAN]).encode("utf-8") and "لوله‌کشی آرمان".encode("utf-8") in as_json.stdout_bytes
    as_csv = export("--format", "csv", "--country", "\u0627\u064a\u0631\u0627\u0646")  # Arabic yeh matches Persian yeh
    assert as_csv.stdout_bytes == leads_to_csv([ARMAN, CASPIAN]).encode("utf-8")
    bom = export("--format", "csv", "--language", "fa", "--excel-bom")
    assert bom.stdout_bytes == b"\xef\xbb\xbf" + leads_to_csv([ARMAN, CASPIAN]).encode("utf-8")
    nasty = export("--format", "csv", "--domain", "nasty.example")
    assert next(csv.DictReader(io.StringIO(nasty.stdout_bytes.decode("utf-8"), newline="")))["business_name"] == 'Say "hi", friend'


# ---------------------------------------------------------------- 6. multiple leads, determinism, files
def test_multiple_leads_are_in_domain_order_whatever_the_input_order():
    expected = leads_to_json(ALL).encode("utf-8")
    for order in ([BARE, CASPIAN, ACME, BOLT, ARMAN], list(reversed(ALL))):
        assert render_export(select_leads(FakeRepository(order)), "json") == expected
        assert render_export(select_leads(FakeRepository(order)), "csv") == leads_to_csv(ALL).encode("utf-8")


def test_cli_multiple_leads_and_determinism(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, [CASPIAN, BARE, ARMAN, BOLT, ACME])
    runs = {fmt: {export("--format", fmt).stdout_bytes for _ in range(3)} for fmt in ("json", "csv")}
    assert all(len(outputs) == 1 for outputs in runs.values())
    assert domains_in_json(runs["json"].pop()) == [lead.domain for lead in ALL]
    assert domains_in_csv(runs["csv"].pop()) == [lead.domain for lead in ALL]


def test_cli_file_output_equals_standard_output(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    for fmt, name in (("json", "out.json"), ("csv", "out.csv")):
        target = tmp_path / name
        result = export("--format", fmt, "--language", "fa", "--output", str(target))
        assert result.exit_code == 0 and f"wrote 2 lead(s) to {target}" in result.output
        assert result.stdout_bytes.decode("utf-8").startswith("wrote 2")  # nothing but the confirmation on stdout
        assert target.read_bytes() == export("--format", fmt, "--language", "fa").stdout_bytes
    bom_target = tmp_path / "bom.csv"
    assert export("--format", "csv", "--excel-bom", "-o", str(bom_target)).exit_code == 0
    assert bom_target.read_bytes() == b"\xef\xbb\xbf" + leads_to_csv(ALL).encode("utf-8")
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith("leads.db")) == ["bom.csv", "out.csv", "out.json"]  # no temp files


def test_write_export_matches_render_export(tmp_path):
    for fmt in ("json", "csv"):
        path = write_export(tmp_path / f"x.{fmt}", ALL, fmt)
        assert path.read_bytes() == render_export(ALL, fmt)
    assert write_export(tmp_path / "b.csv", ALL, "csv", excel_bom=True).read_bytes() == render_export(ALL, "csv", excel_bom=True)


# ---------------------------------------------------------------- 7. invalid export format (and other bad requests)
@pytest.mark.parametrize("bad", ["xml", "yaml", "", "  ", "js on", "excel", "tsv", None, 5])
def test_invalid_format_is_a_clear_error(bad):
    with pytest.raises(LeadExportError, match=r"invalid export format .* \(valid: json, csv\)"):
        render_export(ALL, bad)
    with pytest.raises(LeadExportError):
        write_export("/nonexistent/never.json", ALL, bad)


@pytest.mark.parametrize("bad", ["xml", "yaml", "", "tsv"])
def test_cli_invalid_format_exits_2_and_touches_nothing(monkeypatch, tmp_path, bad):
    url = cli_database(monkeypatch, tmp_path, ALL)
    state, digest = stored_state(url), file_digest(url)
    target = tmp_path / "never.out"
    result = export("--format", bad, "--output", str(target))
    assert result.exit_code == 2, result.output
    assert f"invalid export format {bad!r} (valid: json, csv)" in result.output and "Traceback" not in result.output
    assert result.stdout_bytes == b"" and not target.exists()
    assert stored_state(url) == state and file_digest(url) == digest


def test_cli_invalid_format_and_filter_are_reported_before_the_database_is_used(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, create_tables=False)  # would be exit 1 (no leads table) if it got that far
    assert export("--format", "xml").exit_code == 2
    bad_filter = export("--format", "csv", "--page-type", "spaceship")
    assert bad_filter.exit_code == 2 and "invalid filter:" in bad_filter.output
    assert export("--format", "json", "--excel-bom").exit_code == 2
    assert export("--format", "json").exit_code == 1  # valid request, database not initialised


def test_cli_excel_bom_with_json_is_an_error(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    result = export("--format", "json", "--excel-bom")
    assert result.exit_code == 2 and "CSV only" in result.output and result.stdout_bytes == b""


def test_cli_unwritable_output_is_a_clear_error(monkeypatch, tmp_path):
    cli_database(monkeypatch, tmp_path, ALL)
    missing_dir = tmp_path / "no_such_dir" / "out.json"
    result = export("--format", "json", "--output", str(missing_dir))
    assert result.exit_code == 1 and "cannot write" in result.output and "Traceback" not in result.output
    assert not missing_dir.exists()
    assert export("--format", "json", "--output", str(tmp_path)).exit_code == 1  # a directory


def test_invalid_filter_values_still_raise_the_10_3_error():
    with pytest.raises(LeadFilterError):
        select_leads(FakeRepository(ALL), LeadFilter(page_type="spaceship"))


# ---------------------------------------------------------------- no modification, wiring
def test_export_does_not_change_the_database(monkeypatch, tmp_path):
    url = cli_database(monkeypatch, tmp_path, ALL)
    state, digest = stored_state(url), file_digest(url)
    for args in ([], ["-f", "csv"], ["--language", "fa"], ["-f", "csv", "--language", "zz"], ["-o", str(tmp_path / "o.json")]):
        assert export(*args).exit_code == 0
    export("--format", "xml")
    assert stored_state(url) == state and len(state) == 5 and file_digest(url) == digest


def test_in_memory_leads_are_not_changed_by_export():
    before = [lead.to_dict() for lead in ALL]
    render_export(ALL, "json"), render_export(ALL, "csv")
    assert [lead.to_dict() for lead in ALL] == before


def test_export_help_offers_only_json_and_csv():
    result = export("--help")
    assert result.exit_code == 0
    for option in ("--format", "--output", "--excel-bom", "--language", "--has-email"):
        assert option in result.output
    for option in ("--xml", "--yaml", "--score", "--limit"):  # 11.5 added --sort-by/--page/--page-size
        assert option not in result.output
    assert "export" in runner.invoke(app, ["leads", "--help"]).output
