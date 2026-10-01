"""Read-only inspection of persisted crawl state (Phase 6.3.3).

Everything is derived from the existing `crawls` and `pages` rows (see `app/crawler/resume.py` for what
the page statuses mean); nothing is stored twice, written, or fetched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.crawler.lifecycle import CrawlLifecycle, lifecycle_of
from app.models import CrawlStatus, PageStatus
from app.repositories.crawl import CrawlRepository
from app.repositories.page import PageRepository


@dataclass(frozen=True)
class CrawlStateReport:
    crawl_id: int
    status: CrawlStatus  # crawl completion status (completed / running = resumable / failed ...)
    lifecycle: CrawlLifecycle  # created / running / completed / stopped (resumable) / failed
    stop_reason: str | None  # why the last run stopped (completed, max_pages, max_crawl_time, cancelled, ...)
    seed_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_update_at: datetime  # latest change to the crawl row or any of its pages
    last_checkpoint_at: datetime | None
    elapsed_seconds: float  # crawl time used over all runs
    current_depth: int
    total_pages: int  # persisted page rows == discovered URLs
    visited: int  # requested already (successful + failed); never requested again on resume
    successful: int
    failed: int
    pending: int  # queued, not yet requested
    skipped: int  # discovered but left out by max_pages

    @property
    def discovered(self) -> int:
        return self.total_pages

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict with a fixed key order (datetimes as ISO-8601 strings)."""
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "crawl_id": self.crawl_id,
            "status": self.status.value,
            "lifecycle": self.lifecycle.value,
            "stop_reason": self.stop_reason,
            "seed_url": self.seed_url,
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "last_update_at": iso(self.last_update_at),
            "last_checkpoint_at": iso(self.last_checkpoint_at),
            "elapsed_seconds": self.elapsed_seconds,
            "current_depth": self.current_depth,
            "total_pages": self.total_pages,
            "discovered": self.discovered,
            "visited": self.visited,
            "successful": self.successful,
            "failed": self.failed,
            "pending": self.pending,
            "skipped": self.skipped,
        }


def _aware(value: datetime | None) -> datetime | None:
    """SQLite aggregates (MAX) come back naive; the stored values are UTC."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def inspect_crawl_state(session: Session, crawl_id: int) -> CrawlStateReport | None:
    """Summarise the persisted state of one crawl, or return None if the crawl does not exist.

    Pure reads: no writes, no flush of anything of its own, no network. The same stored state always
    yields the same report.
    """
    crawl = CrawlRepository(session).get(crawl_id)
    if crawl is None:
        return None
    pages = PageRepository(session)
    counts = pages.count_by_status(crawl_id)
    n = lambda status: counts.get(status, 0)  # noqa: E731
    last_page_update = _aware(pages.last_updated(crawl_id))
    return CrawlStateReport(
        crawl_id=crawl.id,
        status=crawl.status,
        lifecycle=lifecycle_of(crawl.status, crawl.stop_reason),
        stop_reason=crawl.stop_reason.value if crawl.stop_reason is not None else None,
        seed_url=crawl.seed_url,
        created_at=crawl.created_at,
        started_at=crawl.started_at,
        finished_at=crawl.finished_at,
        last_update_at=max(t for t in (_aware(crawl.updated_at), last_page_update) if t is not None),
        last_checkpoint_at=crawl.last_checkpoint_at,
        elapsed_seconds=crawl.elapsed_seconds,
        current_depth=crawl.current_depth,
        total_pages=sum(counts.values()),
        visited=n(PageStatus.FETCHED) + n(PageStatus.FAILED),
        successful=n(PageStatus.FETCHED),
        failed=n(PageStatus.FAILED),
        pending=n(PageStatus.PENDING),
        skipped=n(PageStatus.SKIPPED),
    )
