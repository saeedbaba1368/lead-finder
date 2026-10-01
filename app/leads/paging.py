"""Deterministic Lead sorting and pagination (Phase 10.4). Pure and offline: no network, clock, randomness, scoring or
ranking; stored leads are never changed (`BusinessLead` is frozen, the repository is only read).

`sort_leads(leads, sort_by, descending)` orders leads by one existing field:

| `sort_by`       | orders by                                                                   |
|-----------------|-----------------------------------------------------------------------------|
| `business_name` | the name, compared after NFKC + case folding + whitespace collapsing        |
| `domain`        | the canonical domain (`lead_identity`)                                      |
| `first_seen`    | the moment, compared as an instant (any UTC offset)                         |
| `last_seen`     | the moment, compared as an instant (any UTC offset)                         |

Leads without a value for the field (no name, no `first_seen`/`last_seen`) always come last, in both directions, so
that reversing the order never puts unknown values in front of known ones.

Ties are broken by the canonical domain (the lead identity key, always unique per stored lead) and then by the exact
website text, always ascending, whatever the direction. The result therefore never depends on input order. Python's
sort is stable, and the complete key makes it total for distinct leads; two leads with identical identity and website
are interchangeable duplicates.

`paginate(items, page, page_size)` returns a `Page` (`items`, `page`, `page_size`, `total`, `total_pages`, `has_next`,
`has_previous`). Pages are 1-based. A page beyond the last one, and an empty result, give an empty `items` tuple, never
an error; `total_pages` is 0 for an empty result. `page` and `page_size` must be integers (not `bool`), `page >= 1` and
`1 <= page_size <= MAX_PAGE_SIZE` (1000), otherwise `LeadPagingError` (a `LeadDataError`) names the argument and value.
An unknown `sort_by` raises `LeadPagingError` listing the valid fields; `descending` must be a bool.

`sort_and_paginate(leads, ...)` and `sort_and_paginate_stored(repository, ...)` do both. Arguments are validated before
anything is read, so a bad request never touches the database. Sorting and paging compose with 10.3 filtering:
`sort_and_paginate(filter_leads(leads, ...), ...)`.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.leads.errors import LeadDataError
from app.leads.model import BusinessLead

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository

SORT_FIELDS = ("business_name", "domain", "first_seen", "last_seen")
DEFAULT_SORT = "domain"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000
_BATCH = 500


class LeadPagingError(LeadDataError):
    """Invalid sort or pagination arguments. The message names the argument and the value."""


def _sort_field(sort_by: object) -> str:
    if not isinstance(sort_by, str) or sort_by not in SORT_FIELDS:
        raise LeadPagingError(f"sort_by: unknown field {sort_by!r} (valid: {', '.join(SORT_FIELDS)})")
    return sort_by


def _direction(descending: object) -> bool:
    if not isinstance(descending, bool):
        raise LeadPagingError(f"descending: expected True or False, got {type(descending).__name__} ({descending!r})")
    return descending


def _positive_int(name: str, value: object, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeadPagingError(f"{name}: expected a whole number, got {type(value).__name__} ({value!r})")
    if value < 1:
        raise LeadPagingError(f"{name}: must be at least 1, got {value}")
    if maximum is not None and value > maximum:
        raise LeadPagingError(f"{name}: must be at most {maximum}, got {value}")
    return value


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _value(lead: BusinessLead, field: str) -> Any:
    """The comparable value of `field`, or None when the lead has none."""
    if field == "domain":
        return lead.identity.key
    if field == "business_name":
        folded = _fold(lead.business_name) if lead.business_name else ""
        return folded or None
    moment = getattr(lead, field)
    return moment if isinstance(moment, datetime) else None


def sort_leads(leads: Iterable[BusinessLead], sort_by: str = DEFAULT_SORT, descending: bool = False) -> list[BusinessLead]:
    """The leads ordered by `sort_by` (module docstring). A new list; the input and the leads are untouched."""
    field, reverse = _sort_field(sort_by), _direction(descending)
    items = list(leads)
    for item in items:
        if not isinstance(item, BusinessLead):
            raise LeadPagingError(f"can only sort BusinessLead values, got {type(item).__name__}")
    tie = lambda lead: (lead.identity.key, lead.website)  # noqa: E731  (ascending in both directions)
    known = [lead for lead in items if _value(lead, field) is not None]
    unknown = [lead for lead in items if _value(lead, field) is None]
    known.sort(key=tie)  # tie-break first; the stable sort below keeps it among equal values
    known.sort(key=lambda lead: _value(lead, field), reverse=reverse)
    unknown.sort(key=tie)
    return known + unknown


@dataclass(frozen=True, slots=True)
class Page:
    """One page of results plus the numbers needed to navigate."""

    items: tuple[BusinessLead, ...]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        return -(-self.total // self.page_size)

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        return self.page > 1 and self.total > 0


def paginate(items: Iterable[BusinessLead], page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> Page:
    """Slice `items` (kept in the given order) into the requested 1-based page."""
    number, size = _positive_int("page", page), _positive_int("page_size", page_size, MAX_PAGE_SIZE)
    everything = list(items)
    start = (number - 1) * size
    return Page(items=tuple(everything[start:start + size]), page=number, page_size=size, total=len(everything))


def sort_and_paginate(
    leads: Iterable[BusinessLead], sort_by: str = DEFAULT_SORT, descending: bool = False, page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Page:
    """Validate every argument, sort, then return the requested page."""
    _sort_field(sort_by), _direction(descending)
    _positive_int("page", page), _positive_int("page_size", page_size, MAX_PAGE_SIZE)
    return paginate(sort_leads(leads, sort_by, descending), page, page_size)


def sort_and_paginate_stored(
    repository: LeadRepository, sort_by: str = DEFAULT_SORT, descending: bool = False, page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Page:
    """`sort_and_paginate` over every stored lead. Read-only; a bad request fails before the database is read."""
    _sort_field(sort_by), _direction(descending)
    _positive_int("page", page), _positive_int("page_size", page_size, MAX_PAGE_SIZE)
    leads: list[BusinessLead] = []
    offset = 0
    while True:
        batch = repository.load_all(limit=_BATCH, offset=offset)
        leads.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
    return sort_and_paginate(leads, sort_by, descending, page, page_size)
