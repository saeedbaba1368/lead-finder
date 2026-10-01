"""Multi-page lead aggregation (Phase 9.4): the combined lead of one website PLUS what is known about where each
value came from and which values disagreed. Pure and deterministic: no network, clock, database, fuzzy matching,
enrichment or AI.

`aggregate_pages(leads, website=None)` takes the page-level `BusinessLead` values of one canonical website (e.g.
homepage: name + email, contact page: phone + address, about page: description) and returns an `AggregatedLead`:

- `lead`: exactly `aggregate_leads(leads, website=...)` (same rules, same order-independence): contacts are unioned
  without duplicates, a missing name/description/address is filled by the first page that has one, and an existing
  value is never replaced by a later page (canonical page order: homepage, then `source_url`, then JSON text).
- `conflicts`: every case where a page offered a *different* non-empty `business_name`, `description` or `address`
  than the value that was kept. Nothing is lost silently: the kept value, the rejected value and their source URLs
  are listed. Comparison is exact (after the lead model's own normalisation); similar-looking values are different.
  A value that two pages state identically is not a conflict. Several contact values are not conflicts either: a
  business can have many emails, phones and profiles.
- `field_sources`: the source URL of the page that supplied each kept scalar field.
- `value_sources`: for every email (`emails:<address>`), phone (`phones:<number>`) and social profile
  (`social_profiles:<url>`) the sorted source URLs of all pages that contained it.

Sources are only what the model carries (`BusinessLead.source_url`); a page without one contributes no source. The
result is not persisted: the `leads` table keeps the lead and its single `source_url` as before.

`lead_conflicts(leads)` does the conflict check alone for leads given in priority order (first = kept), which is how
`LeadRepository.merge` reports a stored value that a new page disagrees with.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from app.leads.aggregate import _canonical_key, aggregate_leads
from app.leads.errors import LeadDataError
from app.leads.model import BusinessLead

CONFLICT_FIELDS = ("business_name", "description", "address")


@dataclass(frozen=True, slots=True)
class LeadConflict:
    """A page offered `rejected` for `field` while `kept` was already in the lead. `kept` is never replaced."""

    field: str
    kept: str | dict[str, Any]
    rejected: str | dict[str, Any]
    kept_source: str | None = None
    rejected_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field, "kept": self.kept, "rejected": self.rejected,
            "kept_source": self.kept_source, "rejected_source": self.rejected_source,
        }


@dataclass(frozen=True, slots=True)
class AggregatedLead:
    lead: BusinessLead
    conflicts: tuple[LeadConflict, ...] = ()
    field_sources: dict[str, str | None] = dc_field(default_factory=dict)
    value_sources: dict[str, tuple[str, ...]] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead": self.lead.to_dict(),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "field_sources": dict(self.field_sources),
            "value_sources": {k: list(v) for k, v in self.value_sources.items()},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def _value(lead: BusinessLead, field: str) -> str | dict[str, Any] | None:
    if field == "address":
        return lead.address.to_dict() if lead.address is not None else None
    return getattr(lead, field)


def _check(items: Iterable[object]) -> list[BusinessLead]:
    leads = list(items)
    for item in leads:
        if not isinstance(item, BusinessLead):
            raise LeadDataError(f"leads must contain BusinessLead values, got {type(item).__name__}")
    return leads


def lead_conflicts(leads: Iterable[BusinessLead]) -> tuple[LeadConflict, ...]:
    """Conflicts among `leads` given in priority order: for each field the first non-empty value is kept and every
    different later value is reported once (the first page offering it names the source)."""
    ordered = _check(leads)
    found: list[LeadConflict] = []
    for field in CONFLICT_FIELDS:
        kept: tuple[Any, str | None] | None = None
        seen: list[Any] = []
        for lead in ordered:
            value = _value(lead, field)
            if value is None:
                continue
            if kept is None:
                kept = (value, lead.source_url)
                seen.append(value)
            elif value not in seen:
                seen.append(value)
                found.append(LeadConflict(field, kept[0], value, kept[1], lead.source_url))
    return tuple(found)


def aggregate_pages(leads: Iterable[BusinessLead], *, website: str | None = None) -> AggregatedLead:
    """Combine the page-level leads of one website (rules in the module docstring). Raises `LeadDataError` for no
    leads, a non-lead, an unusable `website`, or leads of different canonical domains (never merged)."""
    items = _check(leads)
    combined = aggregate_leads(items, website=website)  # validates the input and the single domain
    ordered = sorted(items, key=_canonical_key)

    field_sources: dict[str, str | None] = {}
    for field in CONFLICT_FIELDS:
        kept = _value(combined, field)
        if kept is not None:
            field_sources[field] = next(lead.source_url for lead in ordered if _value(lead, field) == kept)

    sources: dict[str, set[str]] = {}
    for lead in ordered:
        if lead.source_url is None:
            continue
        for email in lead.emails:
            sources.setdefault(f"emails:{email}", set()).add(lead.source_url)
        for phone in lead.phones:
            sources.setdefault(f"phones:{phone.number}", set()).add(lead.source_url)
        for link in lead.social_profiles:
            sources.setdefault(f"social_profiles:{link.url.casefold()}", set()).add(lead.source_url)
    # keys of social profiles use the profile URL as kept in the combined lead
    kept_social = {link.url.casefold(): link.url for link in combined.social_profiles}
    value_sources = {
        (f"social_profiles:{kept_social[key.split(':', 1)[1]]}" if key.startswith("social_profiles:") else key): tuple(sorted(urls))
        for key, urls in sources.items()
    }
    return AggregatedLead(
        lead=combined,
        conflicts=lead_conflicts(ordered),
        field_sources=field_sources,
        value_sources=dict(sorted(value_sources.items())),
    )
