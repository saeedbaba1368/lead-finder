from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.crawl import Crawl
    from app.models.email import Email
    from app.models.lead import Lead
    from app.models.pattern import EmailPattern
    from app.models.person import Person


class Domain(IdMixin, TimestampMixin, Base):
    """A registrable domain we collect data about (stored lowercase)."""

    __tablename__ = "domains"
    __table_args__ = (
        CheckConstraint("name = lower(name)", name="name_lowercase"),
        CheckConstraint("length(name) > 0", name="name_not_empty"),
    )

    name: Mapped[str] = mapped_column(String(253), unique=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    _cascade = {"cascade": "all, delete-orphan", "passive_deletes": True}
    crawls: Mapped[list[Crawl]] = relationship(back_populates="domain", order_by="Crawl.id", **_cascade)
    emails: Mapped[list[Email]] = relationship(back_populates="domain", order_by="Email.id", **_cascade)
    leads: Mapped[list[Lead]] = relationship(back_populates="domain", order_by="Lead.id", **_cascade)
    people: Mapped[list[Person]] = relationship(back_populates="domain", order_by="Person.id", **_cascade)
    email_patterns: Mapped[list[EmailPattern]] = relationship(
        back_populates="domain", order_by="EmailPattern.id", **_cascade
    )

    @validates("name")
    def _normalize_name(self, _key: str, value: str) -> str:
        return value.strip().lower()
