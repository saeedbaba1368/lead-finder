from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigInt, Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.domain import Domain


class Lead(IdMixin, TimestampMixin, Base):
    """The stored form of one `BusinessLead` (Phase 8.4): exactly one lead per domain.

    Identity = the registrable domain (the existing `domains` row), so the same site can never get two lead rows.
    List-like values are stored as JSON exactly as `BusinessLead.to_dict()` writes them.
    """

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint("length(website) > 0", name="website_not_empty"),
        CheckConstraint("first_seen IS NULL OR last_seen IS NULL OR last_seen >= first_seen", name="last_seen_after_first_seen"),
        Index("ix_leads_crawl_id", "crawl_id"),
        Index("ix_leads_business_name", "business_name"),
    )

    domain_id: Mapped[int] = mapped_column(BigInt, ForeignKey("domains.id", ondelete="CASCADE"), unique=True)
    crawl_id: Mapped[int | None] = mapped_column(BigInt, ForeignKey("crawls.id", ondelete="SET NULL"))  # last crawl that saved it
    website: Mapped[str] = mapped_column(Text)
    business_name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    emails: Mapped[list[str]] = mapped_column(JSON, default=list)
    phones: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)  # [{number, raw}]
    address: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    social_profiles: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)  # [{platform, url}]
    page_type: Mapped[str | None] = mapped_column(String(32))  # a PageCategory value
    language: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    website_metadata: Mapped[dict[str, str | None] | None] = mapped_column(JSON)  # Phase 12.1: WebsiteMetadata.to_dict()

    domain: Mapped[Domain] = relationship(back_populates="leads")
