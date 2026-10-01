from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigInt, Base, IdMixin, TimestampMixin
from app.models.enums import CrawlStatus, CrawlStopReason, enum_type

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.domain import Domain
    from app.models.page import Page


class Crawl(IdMixin, TimestampMixin, Base):
    """One crawl run against a domain (data model only; no crawler yet)."""

    __tablename__ = "crawls"
    __table_args__ = (
        CheckConstraint("max_pages > 0", name="max_pages_positive"),
        CheckConstraint("max_depth >= 0", name="max_depth_non_negative"),
        CheckConstraint("pages_crawled >= 0", name="pages_crawled_non_negative"),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        CheckConstraint("current_depth >= 0", name="current_depth_non_negative"),
        CheckConstraint("elapsed_seconds >= 0", name="elapsed_seconds_non_negative"),
        CheckConstraint("max_crawl_time IS NULL OR max_crawl_time > 0", name="max_crawl_time_positive"),
        CheckConstraint("request_delay >= 0", name="request_delay_non_negative"),
        Index("ix_crawls_domain_id_status", "domain_id", "status"),
        Index("ix_crawls_status_created_at", "status", "created_at"),
        Index("ix_crawls_api_key_id", "api_key_id"),
    )

    domain_id: Mapped[int] = mapped_column(BigInt, ForeignKey("domains.id", ondelete="CASCADE"))
    api_key_id: Mapped[int | None] = mapped_column(BigInt, ForeignKey("api_keys.id", ondelete="SET NULL"))
    status: Mapped[CrawlStatus] = mapped_column(
        enum_type(CrawlStatus), default=CrawlStatus.PENDING, server_default=CrawlStatus.PENDING.value
    )
    seed_url: Mapped[str | None] = mapped_column(Text)
    max_pages: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    max_depth: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    # Resume state (Phase 6.3.2.2.2). max_pages / max_depth above are also part of the resume config.
    max_crawl_time: Mapped[float | None] = mapped_column(Float)  # seconds; NULL = unlimited
    request_delay: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")  # seconds
    current_depth: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # BFS level in progress
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")  # crawl time used so far
    stop_reason: Mapped[CrawlStopReason | None] = mapped_column(enum_type(CrawlStopReason))
    last_checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    domain: Mapped[Domain] = relationship(back_populates="crawls")
    api_key: Mapped[ApiKey | None] = relationship(back_populates="crawls")
    pages: Mapped[list[Page]] = relationship(
        back_populates="crawl", cascade="all, delete-orphan", passive_deletes=True, order_by="Page.id"
    )
