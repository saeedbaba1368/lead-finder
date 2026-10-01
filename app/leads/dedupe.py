"""Exact Lead de-duplication (Phase 9.3). Pure: no network, clock, randomness, database or AI.

Two leads are duplicates only if their identities (`app.leads.lead_identity`, the canonical domain) are identical
strings. There is no fuzzy, name or similarity matching: `acme.example` and `acme-plumbing.example` stay two leads,
and so do two tenants of one hosting platform.

`merge_leads(existing, incoming)` folds a newer sighting of a lead into the one already known. The existing lead
wins; the incoming one only adds:

- `website`, `business_name`, `description`, `address`: kept when the existing lead has a value, else taken from the
  incoming lead. An address is taken whole, never mixed field by field.
- `page_type` + `source_url`: one unit (they describe the same page). The existing pair is kept when either is set,
  otherwise the incoming pair is taken.
- `language`: kept unless missing or `unknown`, then the incoming language fills it.
- `emails`, `phones`, `social_profiles`: union. A value already present keeps its existing spelling (`raw` of a phone,
  case of a social URL). Sorted as in `BusinessLead`.
- `first_seen` = earliest, `last_seen` = latest of the values present (the range never shrinks).

Merging the same lead again changes nothing (idempotent), and merging a lead with itself returns an equal lead.
Leads of different identities raise `LeadDataError`; they are never merged.

`deduplicate_leads(leads)` collapses an iterable to one lead per identity. Within one identity, leads are folded in
input order (the first one is "existing"). The result is ordered by identity key, so it does not depend on the order in
which different sites arrive.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.crawler.html_parser import PhoneNumber
from app.crawler.social import SocialLink
from app.leads.errors import LeadDataError
from app.leads.model import BusinessLead

UNKNOWN_LANGUAGE = "unknown"


def _check(value: object) -> BusinessLead:
    if not isinstance(value, BusinessLead):
        raise LeadDataError(f"leads must be BusinessLead values, got {type(value).__name__}")
    return value


def _phones(existing: BusinessLead, incoming: BusinessLead) -> tuple[PhoneNumber, ...]:
    by_number = {phone.number: phone for phone in incoming.phones}
    by_number.update({phone.number: phone for phone in existing.phones})  # existing spelling wins
    return tuple(by_number[number] for number in sorted(by_number))


def _social(existing: BusinessLead, incoming: BusinessLead) -> tuple[SocialLink, ...]:
    by_url: dict[str, SocialLink] = {}
    for link in (*existing.social_profiles, *incoming.social_profiles):  # existing first: it keeps its spelling
        by_url.setdefault(link.url.casefold(), link)
    return tuple(sorted(by_url.values(), key=lambda link: (link.platform, link.url)))


def _language(existing: str | None, incoming: str | None) -> str | None:
    if existing and existing != UNKNOWN_LANGUAGE:
        return existing
    return incoming or existing


def merge_leads(existing: BusinessLead, incoming: BusinessLead) -> BusinessLead:
    """`existing` plus whatever `incoming` adds (rules in the module docstring). Raises `LeadDataError`."""
    existing, incoming = _check(existing), _check(incoming)
    if existing.identity != incoming.identity:
        raise LeadDataError(
            f"leads with different identities are never merged: {existing.identity.domain} and {incoming.identity.domain}"
        )
    keep_page = existing.page_type is not None or existing.source_url is not None
    page = existing if keep_page else incoming
    firsts = [t for t in (existing.first_seen, incoming.first_seen) if t is not None]
    lasts = [t for t in (existing.last_seen, incoming.last_seen) if t is not None]
    return BusinessLead(
        website=existing.website,
        business_name=existing.business_name or incoming.business_name,
        description=existing.description or incoming.description,
        emails=(*existing.emails, *incoming.emails),
        phones=_phones(existing, incoming),
        address=existing.address if existing.address is not None else incoming.address,
        social_profiles=_social(existing, incoming),
        page_type=page.page_type,
        language=_language(existing.language, incoming.language),
        source_url=page.source_url,
        first_seen=min(firsts) if firsts else None,
        last_seen=max(lasts) if lasts else None,
    )


def deduplicate_leads(leads: Iterable[BusinessLead]) -> list[BusinessLead]:
    """One lead per identity, ordered by identity key. Raises `LeadDataError` for a value that is not a lead."""
    merged: dict[str, BusinessLead] = {}
    for lead in leads:
        lead = _check(lead)
        key = lead.identity.key
        merged[key] = merge_leads(merged[key], lead) if key in merged else lead
    return [merged[key] for key in sorted(merged)]
