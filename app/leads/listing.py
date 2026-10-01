"""Lead listing (Phase 11.1) and search (11.2): the rows and text behind `leadfinder leads list` / `leads search`.
Read-only and offline.

Nothing new is decided here: the leads come from the Phase 10 query (`query_stored_leads` with `LeadQuery(filter=...)`:
the Phase 10.3 `LeadFilter` if one is given, domain order, no paging), so the listing shows the stored leads in the same
deterministic order as the exports. Without a filter that is every lead. No sorting, paging, scoring, enrichment or
crawling options exist.

`LeadRow` holds the basic fields of one lead: `business_name`, `domain`, `website`, `email_count`, `phone_count`,
`first_seen`, `last_seen`. `format_lead_listing(rows)` renders them as text:

* no leads: the single line `No leads found.`
* otherwise a header line and one line per lead, columns separated by one tab (`COLUMNS` gives the names). Tabs keep the
  output parseable and avoid column alignment, which is unreliable for Persian text (right-to-left, ZWNJ, combining marks).
* a missing value (no name, no timestamp) is `-`; timestamps are ISO 8601 UTC, as in the JSON/CSV exports.
* text is shown with control characters and runs of whitespace replaced by a single space, so a crawled name containing
  a newline, a tab or a terminal escape cannot break the layout or drive the terminal. Persian letters, digits and ZWNJ
  are kept exactly.

The same stored leads always give the same text. The stored data is only read.

Output controls (Phase 11.4): `list_leads_page(repository, flt, sort_by, descending, page, page_size)` runs the same Phase 10
query with the Phase 10.4 sort and pagination (`LeadQuery`, nothing re-implemented) and returns a `LeadListing`: the `rows`
plus `total`, `page`, `page_size`, `total_pages` (the last three None without paging). The text format is unchanged; a page
beyond the last one gives no rows (never an error) and `format_lead_listing(..., empty=listing.empty_message)` explains it.
Invalid sort/page values raise `LeadPagingError` before the database is read. No ranking or scoring exists.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.leads.filters import LeadFilter
from app.leads.model import BusinessLead
from app.leads.paging import DEFAULT_SORT
from app.leads.query import LeadQuery, query_stored_leads

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository

COLUMNS = ("business_name", "domain", "website", "email_count", "phone_count", "first_seen", "last_seen")
EMPTY_MESSAGE = "No leads found."
MISSING = "-"
SEPARATOR = "\t"


@dataclass(frozen=True, slots=True)
class LeadRow:
    """The basic fields of one lead, as listed."""

    business_name: str | None
    domain: str
    website: str
    email_count: int
    phone_count: int
    first_seen: datetime | None
    last_seen: datetime | None

    @classmethod
    def from_lead(cls, lead: BusinessLead) -> LeadRow:
        return cls(
            business_name=lead.business_name, domain=lead.domain, website=lead.website, email_count=len(lead.emails),
            phone_count=len(lead.phones), first_seen=lead.first_seen, last_seen=lead.last_seen,
        )


def list_stored_leads(repository: LeadRepository, flt: LeadFilter | None = None) -> list[LeadRow]:
    """The stored leads (all, or those matching the 10.3 filter `flt`) as `LeadRow`s, in domain order. Read-only."""
    return [LeadRow.from_lead(lead) for lead in query_stored_leads(repository, LeadQuery(filter=flt)).leads]


@dataclass(frozen=True, slots=True)
class LeadListing:
    """The rows of one listing plus the numbers describing the selection (paging fields are None without paging)."""

    rows: tuple[LeadRow, ...]
    total: int
    page: int | None
    page_size: int | None
    total_pages: int | None

    @property
    def paged(self) -> bool:
        return self.page is not None

    @property
    def empty_message(self) -> str:
        """Why `rows` is empty: a page beyond the results is explained, otherwise the plain no-leads message."""
        if self.paged and self.total > 0 and not self.rows:
            return f"No leads on page {self.page} ({self.total} lead(s) in {self.total_pages} page(s) of {self.page_size})."
        return EMPTY_MESSAGE

    @property
    def summary(self) -> str | None:
        """One line of navigation numbers for a paged, non-empty page; None otherwise."""
        if not self.paged or not self.rows:
            return None
        return f"page {self.page} of {self.total_pages} ({len(self.rows)} of {self.total} lead(s), page size {self.page_size})"


def list_leads_page(
    repository: LeadRepository, flt: LeadFilter | None = None, sort_by: str = DEFAULT_SORT, descending: bool = False,
    page: int | None = None, page_size: int | None = None,
) -> LeadListing:
    """Stored leads -> 10.3 filter -> 10.4 sort -> 10.4 page, as `LeadRow`s. Read-only; bad arguments fail before any read.

    Without `page`/`page_size` every match is returned (as `list_stored_leads`); giving either one turns paging on.
    """
    result = query_stored_leads(
        repository, LeadQuery(filter=flt, sort_by=sort_by, descending=descending, page=page, page_size=page_size)
    )
    return LeadListing(
        rows=tuple(LeadRow.from_lead(lead) for lead in result.leads), total=result.total, page=result.page,
        page_size=result.page_size, total_pages=result.total_pages,
    )


def _display(value: object) -> str:
    if value is None:
        return MISSING
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    text = "".join(" " if unicodedata.category(char) == "Cc" else char for char in text)
    return " ".join(text.split()) or MISSING


def format_lead_listing(rows: list[LeadRow], empty: str = EMPTY_MESSAGE) -> str:
    """The listing text (no trailing newline); the `empty` message (`No leads found.`) when there are no rows."""
    if not rows:
        return empty
    lines = [SEPARATOR.join(COLUMNS)]
    lines.extend(SEPARATOR.join(_display(getattr(row, column)) for column in COLUMNS) for row in rows)
    return "\n".join(lines)
