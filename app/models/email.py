from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import BigInt, Base, IdMixin, TimestampMixin
from app.models.enums import EmailEvidenceSource, enum_type

if TYPE_CHECKING:
    from app.models.domain import Domain
    from app.models.page import Page
    from app.models.person import Person
    from app.models.verification import EmailVerification


class Email(IdMixin, TimestampMixin, Base):
    """An email address discovered for a domain (stored lowercase, unique per domain)."""

    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("domain_id", "address"),
        CheckConstraint("address = lower(address)", name="address_lowercase"),
        CheckConstraint("address LIKE '%_@_%'", name="address_format"),
        Index("ix_emails_address", "address"),
    )

    domain_id: Mapped[int] = mapped_column(BigInt, ForeignKey("domains.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String(320))
    local_part: Mapped[str] = mapped_column(String(64))
    is_role_based: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    domain: Mapped[Domain] = relationship(back_populates="emails")
    evidence: Mapped[list[EmailEvidence]] = relationship(
        back_populates="email", cascade="all, delete-orphan", passive_deletes=True, order_by="EmailEvidence.id"
    )
    verifications: Mapped[list[EmailVerification]] = relationship(
        back_populates="email", cascade="all, delete-orphan", passive_deletes=True, order_by="EmailVerification.id"
    )
    # Deleting an email keeps the person (people.email_id -> SET NULL).
    people: Mapped[list[Person]] = relationship(back_populates="email", passive_deletes=True)

    @validates("address")
    def _normalize_address(self, _key: str, value: str) -> str:
        value = value.strip().lower()
        self.local_part = value.rsplit("@", 1)[0]
        return value


class EmailEvidence(IdMixin, TimestampMixin, Base):
    """Where (which page, in what form) an email address was seen."""

    __tablename__ = "email_evidence"
    __table_args__ = (
        UniqueConstraint("email_id", "page_id", "source_type"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("occurrences >= 1", name="occurrences_positive"),
        Index("ix_email_evidence_page_id", "page_id"),
    )

    email_id: Mapped[int] = mapped_column(BigInt, ForeignKey("emails.id", ondelete="CASCADE"))
    page_id: Mapped[int] = mapped_column(BigInt, ForeignKey("pages.id", ondelete="CASCADE"))
    source_type: Mapped[EmailEvidenceSource] = mapped_column(
        enum_type(EmailEvidenceSource), default=EmailEvidenceSource.PAGE_TEXT
    )
    snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    occurrences: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    email: Mapped[Email] = relationship(back_populates="evidence")
    page: Mapped[Page] = relationship(back_populates="email_evidence")
