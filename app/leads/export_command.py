"""Lead export interface (Phase 11.3): the pure parts behind `leadfinder leads export`. Read-only and offline.

No new export format and no new selection logic: the leads are the stored leads (all, or those matching a 10.3
`LeadFilter`) read through the Phase 10 query, and the bytes are exactly the Phase 10.1 JSON document / Phase 10.2 CSV
(`leads_to_json` / `leads_to_csv`, in the query's domain order). Formats: `json`, `csv` (`EXPORT_FORMATS`); anything else
raises `LeadExportError` naming the value and the valid formats.

* `export_format(value)`: validates a format name (case-insensitive, surrounding spaces ignored); `validate_export(fmt, excel_bom)` also checks the BOM option.
* `select_leads(repository, flt=None)`: the selected leads, read-only.
* `render_export(leads, fmt, excel_bom=False)`: the export as UTF-8 bytes (no newline translation on any platform).
  `excel_bom=True` (CSV only) prepends the UTF-8 BOM that makes Excel detect Persian text; with JSON it is an error.
* `write_export(path, leads, fmt, excel_bom=False)`: the same bytes through the atomic writers of 10.1/10.2; nothing is
  written when the export fails, and an existing target is replaced only by a complete file.

An empty selection is a valid export: `{"version": 1, "count": 0, "leads": []}` / the CSV header row alone.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from app.leads.errors import LeadDataError
from app.leads.export import leads_to_json, write_leads_json
from app.leads.export_csv import UTF8_BOM, leads_to_csv, write_leads_csv
from app.leads.filters import LeadFilter
from app.leads.model import BusinessLead
from app.leads.paging import DEFAULT_SORT
from app.leads.query import LeadQuery, QueryResult, query_stored_leads

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository

EXPORT_FORMATS = ("json", "csv")


class LeadExportError(LeadDataError):
    """An invalid export request (unknown format, BOM with JSON). The message names the value."""


def export_format(value: object) -> str:
    """The normalised format name, or `LeadExportError` listing the valid formats."""
    if isinstance(value, str) and value.strip().lower() in EXPORT_FORMATS:
        return value.strip().lower()
    raise LeadExportError(f"invalid export format {value!r} (valid: {', '.join(EXPORT_FORMATS)})")


def validate_export(fmt: object, excel_bom: object = False) -> str:
    """The normalised format; `LeadExportError` for an unknown format or `excel_bom` with JSON."""
    name = export_format(fmt)
    if excel_bom and name != "csv":
        raise LeadExportError("excel_bom (--excel-bom) applies to CSV only")
    return name


def select_leads(repository: LeadRepository, flt: LeadFilter | None = None) -> tuple[BusinessLead, ...]:
    """The stored leads matching `flt` (all when None), in domain order. Read-only."""
    return query_stored_leads(repository, LeadQuery(filter=flt)).leads


def select_lead_page(
    repository: LeadRepository, flt: LeadFilter | None = None, sort_by: str = DEFAULT_SORT, descending: bool = False,
    page: int | None = None, page_size: int | None = None,
) -> QueryResult:
    """Stored leads -> 10.3 filter -> 10.4 sort -> 10.4 page (Phase 11.5), as one `QueryResult`. Read-only.

    Without `page`/`page_size` every match is selected; the defaults give `select_leads` (domain order). Invalid sort/page
    values raise `LeadPagingError` before the database is read.
    """
    return query_stored_leads(
        repository, LeadQuery(filter=flt, sort_by=sort_by, descending=descending, page=page, page_size=page_size)
    )


def render_export(leads: Iterable[BusinessLead], fmt: str, *, excel_bom: bool = False) -> bytes:
    """The export of `leads` as UTF-8 bytes (Phase 10.1 JSON or 10.2 CSV)."""
    name = validate_export(fmt, excel_bom)
    text = leads_to_json(leads, preserve_order=True) if name == "json" else leads_to_csv(leads, preserve_order=True)
    return ((UTF8_BOM if excel_bom else "") + text).encode("utf-8")


def write_export(path: str | os.PathLike[str], leads: Iterable[BusinessLead], fmt: str, *, excel_bom: bool = False) -> Path:
    """Write the export to `path` (UTF-8, atomic) and return the path."""
    name = validate_export(fmt, excel_bom)
    items = list(leads)
    if name == "json":
        return write_leads_json(path, items, preserve_order=True)
    return write_leads_csv(path, items, excel_bom=excel_bom, preserve_order=True)
