from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigInt, Base, IdMixin, TimestampMixin, utcnow
from app.models.enums import VerificationMethod, VerificationStatus, enum_type

if TYPE_CHECKING:
    from app.models.email import Email


class EmailVerification(IdMixin, TimestampMixin, Base):
    """One verification attempt for an email. Many rows per email form its history."""

    __tablename__ = "email_verifications"
    __table_args__ = (
        CheckConstraint("smtp_code IS NULL OR (smtp_code >= 100 AND smtp_code <= 599)", name="smtp_code_range"),
        Index("ix_email_verifications_email_id_checked_at", "email_id", "checked_at"),
        Index("ix_email_verifications_status", "status"),
    )

    email_id: Mapped[int] = mapped_column(BigInt, ForeignKey("emails.id", ondelete="CASCADE"))
    status: Mapped[VerificationStatus] = mapped_column(
        enum_type(VerificationStatus),
        default=VerificationStatus.UNKNOWN,
        server_default=VerificationStatus.UNKNOWN.value,
    )
    method: Mapped[VerificationMethod] = mapped_column(enum_type(VerificationMethod))
    reason: Mapped[str | None] = mapped_column(String(255))
    mx_host: Mapped[str | None] = mapped_column(String(255))
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    is_catch_all: Mapped[bool | None] = mapped_column(Boolean)
    provider: Mapped[str | None] = mapped_column(String(64))  # for third-party checks
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    email: Mapped[Email] = relationship(back_populates="verifications")
