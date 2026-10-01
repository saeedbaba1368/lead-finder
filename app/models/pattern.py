from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigInt, Base, IdMixin, TimestampMixin
from app.models.enums import EmailPatternType, enum_type

if TYPE_CHECKING:
    from app.models.domain import Domain


class EmailPattern(IdMixin, TimestampMixin, Base):
    """An email naming pattern observed for a domain, with supporting sample count."""

    __tablename__ = "email_patterns"
    __table_args__ = (
        UniqueConstraint("domain_id", "pattern"),
        CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    domain_id: Mapped[int] = mapped_column(BigInt, ForeignKey("domains.id", ondelete="CASCADE"))
    pattern: Mapped[EmailPatternType] = mapped_column(enum_type(EmailPatternType))
    sample_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")

    domain: Mapped[Domain] = relationship(back_populates="email_patterns")
