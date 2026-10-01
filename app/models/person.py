from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigInt, Base, IdMixin, TimestampMixin
from app.models.enums import PersonEvidenceSource, enum_type

if TYPE_CHECKING:
    from app.models.domain import Domain
    from app.models.email import Email
    from app.models.page import Page


class Person(IdMixin, TimestampMixin, Base):
    """A person associated with a domain, optionally linked to one of its emails."""

    __tablename__ = "people"
    __table_args__ = (
        CheckConstraint("length(full_name) > 0", name="full_name_not_empty"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_people_domain_id_last_first", "domain_id", "last_name", "first_name"),
        Index("ix_people_email_id", "email_id"),
    )

    domain_id: Mapped[int] = mapped_column(BigInt, ForeignKey("domains.id", ondelete="CASCADE"))
    email_id: Mapped[int | None] = mapped_column(BigInt, ForeignKey("emails.id", ondelete="SET NULL"))
    full_name: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    job_title: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")

    domain: Mapped[Domain] = relationship(back_populates="people")
    email: Mapped[Email | None] = relationship(back_populates="people")
    evidence: Mapped[list[PersonEvidence]] = relationship(
        back_populates="person", cascade="all, delete-orphan", passive_deletes=True, order_by="PersonEvidence.id"
    )


class PersonEvidence(IdMixin, TimestampMixin, Base):
    """Where (which page, in what form) a person was seen."""

    __tablename__ = "person_evidence"
    __table_args__ = (
        UniqueConstraint("person_id", "page_id", "source_type"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_person_evidence_page_id", "page_id"),
    )

    person_id: Mapped[int] = mapped_column(BigInt, ForeignKey("people.id", ondelete="CASCADE"))
    page_id: Mapped[int] = mapped_column(BigInt, ForeignKey("pages.id", ondelete="CASCADE"))
    source_type: Mapped[PersonEvidenceSource] = mapped_column(
        enum_type(PersonEvidenceSource), default=PersonEvidenceSource.PAGE_TEXT
    )
    snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    person: Mapped[Person] = relationship(back_populates="evidence")
    page: Mapped[Page] = relationship(back_populates="person_evidence")
