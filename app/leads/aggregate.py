"""Lead data aggregation (Phase 8.3): combine already extracted values into ONE `BusinessLead`.

Pure and deterministic: no network, no clock, no new extraction. Nothing here reads HTML or changes crawling.

Two entry points:

- `aggregate_leads(leads, website=None)`: merge page-level `BusinessLead` values of one website into one lead.
- `aggregate_parsed_pages(pages, website=None)`: build a lead per already parsed page with
  `BusinessLead.from_parsed_page` (page values + JSON-LD structured values; identity, emails, phones, address,
  social profiles, description, page type, language, source URL) and merge them. One page is the simple case.

Merge rules (the result never depends on the order of the input):

- The leads are put in a canonical order first: a homepage lead first, then by `source_url`, then by their JSON
  text as a last tie-break. The first lead in that order is the *primary* lead.
- `emails`, `phones`, `social_profiles`: union of all leads, then the lead model's own rules remove duplicates
  (emails lower-case; phones by normalised `number`, the smallest `raw` text is kept; social profiles by
  case-insensitive URL). Sorted as in `BusinessLead`.
- `business_name`, `description`, `address`: the first non-empty value in canonical order (so the homepage wins,
  and an inner page only fills a gap). Fields of different addresses are never mixed.
- `language`: the most common known language among the leads (`unknown` only when nothing else is known); ties
  go to the earlier lead in canonical order.
- `page_type` and `source_url`: those of the primary lead, so they always describe the same page.
- `first_seen` / `last_seen`: earliest and latest value present.
- `website`: the explicit `website`, otherwise the primary lead's. Every lead must belong to the same registrable
  domain as the result, otherwise `LeadDataError` is raised (leads of different sites are never merged).

Not here: scoring, verification, enrichment, persistence, cross-website de-duplication.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.crawler.classify import PageCategory
from app.crawler.html_parser import PhoneNumber
from app.crawler.social import SocialLink
from app.leads.errors import LeadDataError
from app.leads.identity import domain_of, normalize_website
from app.leads.model import BusinessLead

if TYPE_CHECKING:  # pragma: no cover
    from app.crawler.html_parser import ParsedPage

UNKNOWN_LANGUAGE = "unknown"


@dataclass(frozen=True, slots=True)
class PageSource:
    """One already parsed page and where it came from."""

    parsed: ParsedPage
    source_url: str
    seen_at: datetime | None = None


def _canonical_key(lead: BusinessLead) -> tuple[int, str, str]:
    homepage = lead.page_type is PageCategory.HOMEPAGE
    return (0 if homepage else 1, lead.source_url or "", lead.to_json())


def _first(values: Iterable[object]) -> object | None:
    return next((value for value in values if value is not None), None)


def _language(leads: list[BusinessLead]) -> str | None:
    known = [lead.language for lead in leads if lead.language and lead.language != UNKNOWN_LANGUAGE]
    if known:
        counts = Counter(known)
        best = max(counts.values())
        return next(language for language in known if counts[language] == best)  # first in canonical order
    return UNKNOWN_LANGUAGE if any(lead.language == UNKNOWN_LANGUAGE for lead in leads) else None


def _phones(leads: list[BusinessLead]) -> tuple[PhoneNumber, ...]:
    raw_by_number: dict[str, str] = {}
    for lead in leads:
        for phone in lead.phones:
            current = raw_by_number.get(phone.number)
            if current is None or phone.raw < current:
                raw_by_number[phone.number] = phone.raw
    return tuple(PhoneNumber(number, raw_by_number[number]) for number in sorted(raw_by_number))


def _social(leads: list[BusinessLead]) -> tuple[SocialLink, ...]:
    links = [link for lead in leads for link in lead.social_profiles]
    return tuple(sorted(links, key=lambda link: (link.platform, link.url)))  # same-URL case variants: smallest wins


def aggregate_leads(leads: Iterable[BusinessLead], *, website: str | None = None) -> BusinessLead:
    """Merge the leads of one website into one `BusinessLead` (rules in the module docstring).

    Raises `LeadDataError` for no leads, a value that is not a `BusinessLead`, an unusable `website`, or leads that
    belong to different registrable domains."""
    items = list(leads)
    for item in items:
        if not isinstance(item, BusinessLead):
            raise LeadDataError(f"leads must contain BusinessLead values, got {type(item).__name__}")
    if not items:
        raise LeadDataError("no leads to aggregate")
    ordered = sorted(items, key=_canonical_key)
    primary = ordered[0]

    site = normalize_website(website, "website") if website is not None else primary.website
    domain = domain_of(site)
    foreign = sorted({lead.domain for lead in ordered if lead.domain != domain}, key=str)
    if foreign:
        raise LeadDataError(f"leads of different websites cannot be aggregated: {domain} and {', '.join(foreign)}")

    first_seen = [lead.first_seen for lead in ordered if lead.first_seen is not None]
    last_seen = [lead.last_seen for lead in ordered if lead.last_seen is not None]
    return BusinessLead(
        website=site,
        business_name=_first(lead.business_name for lead in ordered),  # type: ignore[arg-type]
        description=_first(lead.description for lead in ordered),  # type: ignore[arg-type]
        emails=tuple(email for lead in ordered for email in lead.emails),
        phones=_phones(ordered),
        address=_first(lead.address for lead in ordered),  # type: ignore[arg-type]
        social_profiles=_social(ordered),
        page_type=primary.page_type,
        language=_language(ordered),
        source_url=primary.source_url,
        first_seen=min(first_seen) if first_seen else None,
        last_seen=max(last_seen) if last_seen else None,
    )


def aggregate_parsed_pages(pages: Iterable[PageSource | tuple[ParsedPage, str]], *, website: str | None = None) -> BusinessLead:
    """Aggregate already parsed pages (`PageSource` or `(parsed, source_url)` tuples) of one website into one lead.

    Each page is mapped with `BusinessLead.from_parsed_page` (nothing is fetched or re-extracted), then merged by
    `aggregate_leads`."""
    leads: list[BusinessLead] = []
    for item in pages:
        source = item if isinstance(item, PageSource) else PageSource(*item)
        leads.append(
            BusinessLead.from_parsed_page(source.parsed, source_url=source.source_url, website=website, seen_at=source.seen_at)
        )
    return aggregate_leads(leads, website=website)
