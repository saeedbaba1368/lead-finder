"""Lead CSV export (Phase 10.2). Read-only, offline, deterministic. CSV only: no filtering, no CLI, JSON export untouched.

Format: RFC 4180 style through the standard `csv` module. UTF-8 text, header row always present (also when there are no
leads), `\\r\\n` record separators, a field is quoted only when it needs it (it contains a comma, a double quote, a CR or
a LF), a double quote inside a quoted field is doubled and
line breaks inside a field are kept as they are. Non-ASCII text (Persian, ZWNJ, Persian digits) is written as is.

Columns, always in this order (`COLUMNS`), one row per lead, taken from `BusinessLead.to_dict()` (the same validated
values the JSON export uses; `leads_document` provides the order and the duplicate check):

    business_name, website, domain, description, emails, phones,
    address_street, address_city, address_region, address_postal_code, address_country, address_formatted,
    social_profiles, page_type, language, source_url, first_seen, last_seen

* A missing optional value (`None`), an empty collection and a missing address are an empty cell. A cell is therefore
  empty both for "not known" and for an empty string, which a lead never holds (the model turns blank into None).
* `emails`, `phones` (normalised numbers; the `raw` spelling is not exported) and `social_profiles` (profile URLs)
  hold several values in one cell, joined by `|` in the lead's own sorted order. `|` cannot occur inside these values
  (emails and URLs never contain it unescaped); a value that does contain it raises `LeadDataError` instead of
  producing an ambiguous cell.
* Timestamps are ISO 8601 UTC, as in `to_dict()`. Rows are ordered by canonical domain (lead identity), so output does
  not depend on insertion or input order; the same leads always give the same bytes. Two leads with one identity raise
  `LeadDataError` (nothing is dropped silently).
* Values are written exactly as stored. Spreadsheet programs may treat a text cell that starts with `=`, `+`, `-` or
  `@` as a formula; crawled business names are untrusted text, so open exports in a spreadsheet with that in mind.
  (Not altered here: it would change the data.)

`write_leads_csv(path, leads, excel_bom=False)` writes UTF-8 through a temporary file and an atomic replace.
`excel_bom=True` prepends a UTF-8 BOM, which makes Excel detect UTF-8 (Persian text) when the file is double-clicked.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.leads.errors import LeadDataError
from app.leads.export import leads_document
from app.leads.model import BusinessLead

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository

LIST_SEPARATOR = "|"
ADDRESS_PARTS = ("street", "city", "region", "postal_code", "country", "formatted")
COLUMNS = (
    "business_name", "website", "domain", "description", "emails", "phones",
    *(f"address_{part}" for part in ADDRESS_PARTS),
    "social_profiles", "page_type", "language", "source_url", "first_seen", "last_seen",
)
UTF8_BOM = "\ufeff"
_BATCH = 500


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _joined(values: list[str], column: str) -> str:
    for value in values:
        if LIST_SEPARATOR in value:
            raise LeadDataError(f"{column} value {value!r} contains the list separator {LIST_SEPARATOR!r}")
    return LIST_SEPARATOR.join(values)


def _row(entry: dict[str, Any]) -> list[str]:
    address = entry["address"] or {}
    cells = {
        "business_name": entry["business_name"], "website": entry["website"], "domain": entry["domain"],
        "description": entry["description"],
        "emails": _joined(entry["emails"], "emails"),
        "phones": _joined([p["number"] for p in entry["phones"]], "phones"),
        **{f"address_{part}": address.get(part) for part in ADDRESS_PARTS},
        "social_profiles": _joined([s["url"] for s in entry["social_profiles"]], "social_profiles"),
        "page_type": entry["page_type"], "language": entry["language"], "source_url": entry["source_url"],
        "first_seen": entry["first_seen"], "last_seen": entry["last_seen"],
    }
    return [_cell(cells[column]) for column in COLUMNS]


def leads_to_csv(leads: Iterable[BusinessLead], *, preserve_order: bool = False) -> str:
    """One or many leads as CSV text (header + one row per lead, ordered by domain unless `preserve_order`, Phase 10.5)."""
    entries = leads_document(leads, preserve_order=preserve_order)["leads"]  # validates, sorts by identity, refuses duplicate identities
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, dialect="excel", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    for entry in entries:
        writer.writerow(_row(entry))
    return buffer.getvalue()


def export_stored_leads_csv(repository: LeadRepository) -> str:
    """CSV of every stored lead. Read-only (selects only: no flush, commit or `updated_at` change)."""
    leads: list[BusinessLead] = []
    offset = 0
    while True:
        batch = repository.load_all(limit=_BATCH, offset=offset)
        leads.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
    return leads_to_csv(leads)


def write_leads_csv(
    path: str | os.PathLike[str], leads: Iterable[BusinessLead], *, excel_bom: bool = False, preserve_order: bool = False,
) -> Path:
    """Write the CSV of `leads` to `path` as UTF-8 and return the path. Nothing is written if the export fails."""
    target = Path(path)
    text = leads_to_csv(leads, preserve_order=preserve_order)
    data = ((UTF8_BOM if excel_bom else "") + text).encode("utf-8")  # bytes: no newline translation on any platform
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return target
