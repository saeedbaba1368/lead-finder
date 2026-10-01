"""Lead persistence (Phase 8.4): store, load and update `BusinessLead` values in the `leads` table.

Identity: one lead per registrable domain. The lead row points at the existing `domains` row (created on demand with
`DomainRepository.get_or_create`), and `domains.name` is unique, so two rows for one site cannot exist, not even by
accident (a race is resolved by the unique `leads.domain_id` + re-select, like the other `get_or_create` methods).

Like every repository this one flushes and never commits; the caller owns the transaction. Nothing here fetches
anything, extracts anything or touches crawl/page rows (a lead may reference the crawl that produced it; that link
is cleared, not cascaded, when the crawl is deleted).

`BusinessLead` is imported lazily: `app.leads` imports `app.crawler`, which imports `app.repositories`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.models import Lead
from app.repositories.base import BaseRepository
from app.repositories.domain import DomainRepository

if TYPE_CHECKING:  # pragma: no cover
    from app.leads import BusinessLead
    from app.leads.metadata import WebsiteMetadata

logger = get_logger(__name__)

_VALUE_COLUMNS = (
    "website", "business_name", "description", "emails", "phones", "address", "social_profiles",
    "page_type", "language", "source_url", "first_seen", "last_seen",
)


@dataclass(frozen=True, slots=True)
class SaveResult:
    record: Lead
    created: bool  # a new row was inserted
    changed: bool  # the stored values differ from before (always True when created)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything stored here is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _values(lead: BusinessLead) -> dict[str, Any]:
    data = lead.to_dict()
    return {
        "website": data["website"], "business_name": data["business_name"], "description": data["description"],
        "emails": data["emails"], "phones": data["phones"], "address": data["address"],
        "social_profiles": data["social_profiles"], "page_type": data["page_type"], "language": data["language"],
        "source_url": data["source_url"], "first_seen": lead.first_seen, "last_seen": lead.last_seen,
    }


def to_business_lead(record: Lead) -> BusinessLead:
    """Rebuild the value object from a stored row (`BusinessLead.from_dict`, so it is validated again)."""
    from app.leads import BusinessLead

    def iso(value: datetime | None) -> str | None:
        value = _aware(value)
        return value.isoformat() if value is not None else None

    return BusinessLead.from_dict({
        "website": record.website, "business_name": record.business_name, "domain": record.domain.name,
        "description": record.description, "emails": list(record.emails or []), "phones": list(record.phones or []),
        "address": dict(record.address) if record.address else None, "social_profiles": list(record.social_profiles or []),
        "page_type": record.page_type, "language": record.language, "source_url": record.source_url,
        "first_seen": iso(record.first_seen), "last_seen": iso(record.last_seen),
    })


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    # ------------------------------------------------------------------ reads
    def get_by_domain(self, domain: str) -> Lead | None:
        domains = DomainRepository(self.session)
        row = domains.get_by_name(domain)
        if row is None:
            return None
        return self.session.scalars(select(Lead).where(Lead.domain_id == row.id)).first()

    def load(self, domain: str) -> BusinessLead | None:
        """The stored lead of a registrable domain, or None."""
        record = self.get_by_domain(domain)
        return to_business_lead(record) if record is not None else None

    def load_all(self, *, limit: int = 100, offset: int = 0) -> list[BusinessLead]:
        """Stored leads, oldest row first (deterministic)."""
        return [to_business_lead(record) for record in self.list(limit=limit, offset=offset)]

    # ------------------------------------------------------------------ writes
    def save(self, lead: BusinessLead, *, crawl_id: int | None = None) -> SaveResult:
        """Create the lead of its domain, or update it to exactly the given values (upsert by domain).

        - Saving the same lead again changes nothing (`changed=False`, `updated_at` untouched).
        - `first_seen` never moves forward and `last_seen` never moves back: re-saving older data after a restart
          cannot make the stored time range shrink. A None time keeps the stored one.
        - `crawl_id` (when given) must be an existing crawl; None keeps the stored link.
        """
        domains = DomainRepository(self.session)
        domain, _ = domains.get_or_create(lead.domain)
        values = _values(lead)
        existing = self.session.scalars(select(Lead).where(Lead.domain_id == domain.id)).first()
        if existing is None:
            try:
                with self.session.begin_nested():
                    record = self.add(Lead(domain_id=domain.id, crawl_id=crawl_id, **values))
                return SaveResult(record, created=True, changed=True)
            except IntegrityError:  # another writer created it between the select and the insert
                existing = self.session.scalars(select(Lead).where(Lead.domain_id == domain.id)).first()
                if existing is None:
                    raise
        return SaveResult(existing, created=False, changed=self._apply(existing, values, crawl_id))

    def merge(self, lead: BusinessLead, *, crawl_id: int | None = None) -> SaveResult:
        """Exact de-duplication (Phase 9.3): combine `lead` with the stored lead of the same canonical domain
        (`merge_leads`), or create it. The stored values win; the new lead only adds what is missing (contacts are
        unioned, the seen range only widens). Merging the same data again changes nothing. A different domain is a
        different lead; there is no fuzzy matching."""
        from app.leads import lead_conflicts, merge_leads

        stored = self.load(lead.identity.domain)
        if stored is not None:
            for conflict in lead_conflicts([stored, lead]):  # the stored value is kept; the disagreement is not silent
                logger.warning("lead_conflict", extra={
                    "domain": stored.domain, "field": conflict.field, "rejected_source": conflict.rejected_source,
                })
        combined = merge_leads(stored, lead) if stored is not None else lead
        return self.save(combined, crawl_id=crawl_id)

    def load_metadata(self, domain: str) -> WebsiteMetadata | None:
        """The stored website metadata of a registrable domain's lead, or None (no lead or no metadata)."""
        record = self.get_by_domain(domain)
        return self._stored_metadata(record) if record is not None else None

    def merge_metadata(self, domain: str, metadata: WebsiteMetadata | None) -> bool:
        """Phase 12.1: fold one page's website metadata into the lead of `domain`. The stored value of each field
        wins; the page only fills what is missing (`app.leads.metadata.merge_metadata`). Returns True when the
        stored metadata changed. Needs an existing lead (False otherwise); never touches any other column."""
        from app.leads.metadata import merge_metadata

        record = self.get_by_domain(domain)
        if record is None or metadata is None:
            return False
        stored = self._stored_metadata(record)
        merged = merge_metadata(stored, metadata)
        if merged is None or merged == stored:
            return False
        self.update(record, website_metadata=merged.to_dict())
        return True

    @staticmethod
    def _stored_metadata(record: Lead) -> WebsiteMetadata | None:
        from app.leads.metadata import WebsiteMetadata

        return WebsiteMetadata.from_dict(record.website_metadata) if record.website_metadata else None

    def _apply(self, record: Lead, values: dict[str, Any], crawl_id: int | None) -> bool:
        new = dict(values)
        first, last = _aware(record.first_seen), _aware(record.last_seen)
        if new["first_seen"] is None or (first is not None and first <= new["first_seen"]):
            new["first_seen"] = first
        if new["last_seen"] is None or (last is not None and last >= new["last_seen"]):
            new["last_seen"] = last
        if crawl_id is not None:
            new["crawl_id"] = crawl_id
        changes = {}
        for key, value in new.items():
            current = getattr(record, key)
            if key in ("first_seen", "last_seen"):
                current = _aware(current)
            if current != value:
                changes[key] = value
        if changes:
            self.update(record, **changes)
        return bool(changes)
