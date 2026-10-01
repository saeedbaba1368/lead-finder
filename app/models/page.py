from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigInt, Base, IdMixin, TimestampMixin
from app.models.enums import PageOutcome, PageStatus, enum_type

if TYPE_CHECKING:
    from app.models.crawl import Crawl
    from app.models.email import EmailEvidence
    from app.models.person import PersonEvidence


class Page(IdMixin, TimestampMixin, Base):
    """A URL visited (or queued) within a crawl. Raw page bodies are intentionally not stored."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("crawl_id", "url_hash"),
        CheckConstraint("depth >= 0", name="depth_non_negative"),
        CheckConstraint("http_status IS NULL OR (http_status >= 100 AND http_status <= 599)", name="http_status_range"),
        CheckConstraint("response_size IS NULL OR response_size >= 0", name="response_size_non_negative"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="duration_non_negative"),
        CheckConstraint("redirect_count >= 0", name="redirect_count_non_negative"),
        Index("ix_pages_crawl_id_status", "crawl_id", "status"),
        Index("ix_pages_content_hash", "content_hash"),
        Index("ix_pages_dedupe_key", "dedupe_key"),
    )

    crawl_id: Mapped[int] = mapped_column(BigInt, ForeignKey("crawls.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex of `url` (see hash_url)
    depth: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[PageStatus] = mapped_column(
        enum_type(PageStatus), default=PageStatus.PENDING, server_default=PageStatus.PENDING.value
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    # Fetch metadata (Phase 6.3.2.2.1)
    final_url: Mapped[str | None] = mapped_column(Text)  # URL after redirects
    charset: Mapped[str | None] = mapped_column(String(64))
    response_size: Mapped[int | None] = mapped_column(BigInt)  # bytes actually read
    duration_seconds: Mapped[float | None] = mapped_column(Float)  # whole fetch incl. redirects
    redirect_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    redirect_chain: Mapped[list[dict[str, str | int]] | None] = mapped_column(JSON)  # [{url, status_code, location}]
    outcome: Mapped[PageOutcome | None] = mapped_column(enum_type(PageOutcome))  # failure kind when not OK
    parent_url: Mapped[str | None] = mapped_column(Text)  # page the link was found on (None for the seed); BFS queue state
    dedupe_key: Mapped[str | None] = mapped_column(String(64))  # sha256 hex of the canonical/deduplicated URL

    crawl: Mapped[Crawl] = relationship(back_populates="pages")
    email_evidence: Mapped[list[EmailEvidence]] = relationship(
        back_populates="page", cascade="all, delete-orphan", passive_deletes=True
    )
    person_evidence: Mapped[list[PersonEvidence]] = relationship(
        back_populates="page", cascade="all, delete-orphan", passive_deletes=True
    )

    @staticmethod
    def hash_url(url: str) -> str:
        """sha256 hex digest of the URL exactly as given; callers normalise URLs first."""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()
