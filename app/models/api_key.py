from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.crawl import Crawl


class ApiKey(IdMixin, TimestampMixin, Base):
    """An API credential. Only a hash of the secret is stored, never the secret itself."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True)  # non-secret lookup id
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Deleting a key keeps its crawls (crawls.api_key_id -> SET NULL).
    crawls: Mapped[list[Crawl]] = relationship(back_populates="api_key", passive_deletes=True)

    def is_usable(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if not self.is_active or self.revoked_at is not None:
            return False
        if self.expires_at is not None:
            expires = self.expires_at
            if expires.tzinfo is None:  # SQLite returns naive datetimes
                expires = expires.replace(tzinfo=now.tzinfo)
            return expires > now
        return True
