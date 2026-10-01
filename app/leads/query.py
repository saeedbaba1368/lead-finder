"""Lead query and export integration (Phase 10.5). Pure and offline: no network, clock, randomness, scoring, ranking,
verification or enrichment; stored leads are only read, never changed.

Pipeline (each step is the existing, tested function):

    stored leads -> filter (10.3) -> sort (10.4) -> paginate (10.4) -> export (10.1 JSON / 10.2 CSV)

A `LeadQuery` holds the whole request and validates it when it is created (so a bad request fails before any lead or
database row is read):

* `filter`: a `LeadFilter`, a mapping of criteria (`LeadFilter.from_dict`) or None (every lead).
* `sort_by` (`business_name`, `domain`, `first_seen`, `last_seen`; default `domain`) and `descending`.
* `page` / `page_size`: both None (the default) = no pagination, every matching lead. Giving either one turns paging on;
  the other defaults to page 1 / `DEFAULT_PAGE_SIZE`. Values are validated as in 10.4 (`LeadPagingError`).

`query_leads(leads, query)` / `query_stored_leads(repository, query)` return a `QueryResult`: the selected `leads` (a
tuple, in query order), `total` (matches before paging), `page`, `page_size` (None without paging), `total_pages` (None
without paging). A page beyond the end, and a filter that matches nothing, give an empty selection, never an error.

Exports contain exactly the selected leads, in query order (the sort is kept, not replaced by the domain order of a plain
export), using the unchanged 10.1 / 10.2 formats, so `query_to_json(...)` and `query_to_csv(...)` of one query hold the
same leads in the same order with the same values (one row per entry; `BusinessLead.to_dict()` is the single source for
both). Paging information is not written into the files: the JSON document stays `{version, count, leads}` (`count` is
the number of selected leads) and can be read back with the existing code. `write_query_json` / `write_query_csv` write
atomically as UTF-8, like the plain writers. Two selected leads with one identity raise `LeadDataError`, as in every export.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.leads.errors import LeadDataError
from app.leads.export import leads_to_json, write_leads_json
from app.leads.export_csv import leads_to_csv, write_leads_csv
from app.leads.filters import LeadFilter, LeadFilterError, filter_leads, filter_stored_leads
from app.leads.model import BusinessLead
from app.leads.paging import (
    DEFAULT_PAGE_SIZE, DEFAULT_SORT, MAX_PAGE_SIZE, _direction, _positive_int, _sort_field, sort_leads,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository


@dataclass(frozen=True, slots=True)
class LeadQuery:
    """A complete, validated request: filter, sort and (optional) page. See the module docstring."""

    filter: Any = None
    sort_by: str = DEFAULT_SORT
    descending: bool = False
    page: int | None = None
    page_size: int | None = None

    def __post_init__(self) -> None:
        flt = self.filter
        if flt is None or isinstance(flt, Mapping):
            flt = LeadFilter.from_dict(flt)
        elif not isinstance(flt, LeadFilter):
            raise LeadFilterError(f"filter must be a LeadFilter, a mapping or None, got {type(flt).__name__}")
        object.__setattr__(self, "filter", flt)
        _sort_field(self.sort_by), _direction(self.descending)
        if self.page is not None:
            _positive_int("page", self.page)
        if self.page_size is not None:
            _positive_int("page_size", self.page_size, MAX_PAGE_SIZE)

    @property
    def paged(self) -> bool:
        return self.page is not None or self.page_size is not None

    def to_dict(self) -> dict[str, Any]:
        """The normalised query (for logs and tests); no clock, no ids."""
        return {
            "filter": self.filter.to_dict(), "sort_by": self.sort_by, "descending": self.descending,
            "page": self.page, "page_size": self.page_size,
        }


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The selected leads (query order) and the numbers describing the selection."""

    leads: tuple[BusinessLead, ...]
    total: int
    page: int | None
    page_size: int | None

    @property
    def total_pages(self) -> int | None:
        return None if self.page_size is None else -(-self.total // self.page_size)

    def __len__(self) -> int:
        return len(self.leads)


def _query(query: LeadQuery | Mapping[str, Any] | None, criteria: Mapping[str, Any]) -> LeadQuery:
    if query is None:
        return LeadQuery(**criteria)
    if criteria:
        raise LeadDataError("give either a LeadQuery or keyword arguments, not both")
    if isinstance(query, Mapping):
        unknown = sorted(map(str, set(query) - {"filter", "sort_by", "descending", "page", "page_size"}))
        if unknown:
            raise LeadDataError(f"unknown query fields: {', '.join(unknown)}")
        return LeadQuery(**query)
    if not isinstance(query, LeadQuery):
        raise LeadDataError(f"query must be a LeadQuery, a mapping or None, got {type(query).__name__}")
    return query


def _select(filtered: list[BusinessLead], query: LeadQuery) -> QueryResult:
    ordered = sort_leads(filtered, query.sort_by, query.descending)
    if not query.paged:
        return QueryResult(tuple(ordered), len(ordered), None, None)
    page, size = query.page or 1, query.page_size or DEFAULT_PAGE_SIZE
    start = (page - 1) * size
    return QueryResult(tuple(ordered[start:start + size]), len(ordered), page, size)


def query_leads(leads: Iterable[BusinessLead], query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any) -> QueryResult:
    """Filter, sort and page `leads`. The query may be a `LeadQuery`, a mapping, or keyword fields."""
    request = _query(query, fields)
    return _select(filter_leads(leads, request.filter), request)


def query_stored_leads(repository: LeadRepository, query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any) -> QueryResult:
    """`query_leads` over every stored lead. Read-only (selects only); a bad query fails before the database is read."""
    request = _query(query, fields)
    return _select(filter_stored_leads(repository, request.filter), request)


# ----------------------------------------------------------------------------- export
def query_to_json(leads: Iterable[BusinessLead], query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any) -> str:
    """The JSON export document (10.1 format) of the selected leads, in query order."""
    return leads_to_json(query_leads(leads, query, **fields).leads, preserve_order=True)


def query_to_csv(leads: Iterable[BusinessLead], query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any) -> str:
    """The CSV export (10.2 format) of the selected leads, in query order."""
    return leads_to_csv(query_leads(leads, query, **fields).leads, preserve_order=True)


def export_query_json(repository: LeadRepository, query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any) -> str:
    """Stored leads -> filter -> sort -> paginate -> JSON text. Read-only."""
    return leads_to_json(query_stored_leads(repository, query, **fields).leads, preserve_order=True)


def export_query_csv(repository: LeadRepository, query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any) -> str:
    """Stored leads -> filter -> sort -> paginate -> CSV text. Read-only."""
    return leads_to_csv(query_stored_leads(repository, query, **fields).leads, preserve_order=True)


def write_query_json(
    path: str | os.PathLike[str], repository: LeadRepository, query: LeadQuery | Mapping[str, Any] | None = None, **fields: Any,
) -> Path:
    """Write `export_query_json` to `path` (UTF-8, atomic). Nothing is written if the query or export fails."""
    return write_leads_json(path, query_stored_leads(repository, query, **fields).leads, preserve_order=True)


def write_query_csv(
    path: str | os.PathLike[str], repository: LeadRepository, query: LeadQuery | Mapping[str, Any] | None = None, *,
    excel_bom: bool = False, **fields: Any,
) -> Path:
    """Write `export_query_csv` to `path` (UTF-8, atomic, optional BOM). Nothing is written if the query or export fails."""
    return write_leads_csv(
        path, query_stored_leads(repository, query, **fields).leads, excel_bom=excel_bom, preserve_order=True,
    )
