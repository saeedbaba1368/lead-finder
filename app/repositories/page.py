from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.base import utcnow
from app.models import Page, PageOutcome, PageStatus
from app.repositories.base import BaseRepository


def _fetch_metadata(
    *,
    final_url: str | None,
    charset: str | None,
    response_size: int | None,
    duration_seconds: float | None,
    redirect_count: int | None,
    redirect_chain: list[dict[str, str | int]] | None,
    outcome: PageOutcome | None,
    dedupe_key: str | None,
) -> dict[str, Any]:
    """Fetch-metadata column values that were actually supplied (None means "leave unchanged")."""
    if redirect_count is None and redirect_chain is not None:
        redirect_count = len(redirect_chain)
    values: dict[str, Any] = {
        "final_url": final_url,
        "charset": charset,
        "response_size": response_size,
        "duration_seconds": duration_seconds,
        "redirect_count": redirect_count,
        "redirect_chain": redirect_chain,
        "outcome": outcome,
        "dedupe_key": dedupe_key,
    }
    return {k: v for k, v in values.items() if v is not None}


class PageRepository(BaseRepository[Page]):
    model = Page

    def create(
        self,
        crawl_id: int,
        url: str,
        *,
        depth: int = 0,
        status: PageStatus = PageStatus.PENDING,
        dedupe_key: str | None = None,
        parent_url: str | None = None,
    ) -> Page:
        """Insert a page; ``url_hash`` is derived from ``url`` (callers normalise URLs first)."""
        return self.add(
            Page(
                crawl_id=crawl_id,
                url=url,
                url_hash=Page.hash_url(url),
                depth=depth,
                status=status,
                dedupe_key=dedupe_key,
                parent_url=parent_url,
            )
        )

    def get_by_url(self, crawl_id: int, url: str) -> Page | None:
        stmt = select(Page).where(Page.crawl_id == crawl_id, Page.url_hash == Page.hash_url(url))
        return self.session.scalars(stmt).first()

    def get_or_create(
        self,
        crawl_id: int,
        url: str,
        *,
        depth: int = 0,
        status: PageStatus = PageStatus.PENDING,
        parent_url: str | None = None,
    ) -> tuple[Page, bool]:
        existing = self.get_by_url(crawl_id, url)
        if existing is not None:
            return existing, False
        try:
            with self.session.begin_nested():
                return self.create(crawl_id, url, depth=depth, status=status, parent_url=parent_url), True
        except IntegrityError:
            existing = self.get_by_url(crawl_id, url)
            if existing is None:
                raise
            return existing, False

    def list_for_crawl(
        self, crawl_id: int, *, status: PageStatus | None = None, limit: int = 100, offset: int = 0
    ) -> list[Page]:
        stmt = select(Page).where(Page.crawl_id == crawl_id)
        if status is not None:
            stmt = stmt.where(Page.status == status)
        return list(self.session.scalars(stmt.order_by(Page.id).limit(limit).offset(offset)))

    def count_for_crawl(self, crawl_id: int, *, status: PageStatus | None = None) -> int:
        stmt = select(func.count()).select_from(Page).where(Page.crawl_id == crawl_id)
        if status is not None:
            stmt = stmt.where(Page.status == status)
        return self.session.scalar(stmt) or 0

    def count_by_status(self, crawl_id: int) -> dict[PageStatus, int]:
        """Pages of a crawl per status (statuses without pages are omitted). Read-only."""
        stmt = select(Page.status, func.count()).where(Page.crawl_id == crawl_id).group_by(Page.status)
        return {status: n for status, n in self.session.execute(stmt).all()}

    def last_updated(self, crawl_id: int) -> datetime | None:
        """Most recent `updated_at` among the crawl's pages (None when it has none). Read-only."""
        return self.session.scalar(select(func.max(Page.updated_at)).where(Page.crawl_id == crawl_id))

    def mark_fetched(
        self,
        page: Page,
        *,
        http_status: int,
        content_type: str | None = None,
        content_hash: str | None = None,
        title: str | None = None,
        now: datetime | None = None,
        final_url: str | None = None,
        charset: str | None = None,
        response_size: int | None = None,
        duration_seconds: float | None = None,
        redirect_count: int | None = None,
        redirect_chain: list[dict[str, str | int]] | None = None,
        outcome: PageOutcome | None = PageOutcome.OK,
        dedupe_key: str | None = None,
    ) -> Page:
        return self.update(
            page,
            status=PageStatus.FETCHED,
            http_status=http_status,
            content_type=content_type,
            content_hash=content_hash,
            title=title,
            fetched_at=now or utcnow(),
            error_message=None,
            **_fetch_metadata(
                final_url=final_url,
                charset=charset,
                response_size=response_size,
                duration_seconds=duration_seconds,
                redirect_count=redirect_count,
                redirect_chain=redirect_chain,
                outcome=outcome,
                dedupe_key=dedupe_key,
            ),
        )

    def mark_failed(
        self,
        page: Page,
        error_message: str,
        *,
        http_status: int | None = None,
        final_url: str | None = None,
        charset: str | None = None,
        response_size: int | None = None,
        duration_seconds: float | None = None,
        redirect_count: int | None = None,
        redirect_chain: list[dict[str, str | int]] | None = None,
        outcome: PageOutcome | None = None,
        dedupe_key: str | None = None,
    ) -> Page:
        return self.update(
            page,
            status=PageStatus.FAILED,
            error_message=error_message,
            http_status=http_status,
            **_fetch_metadata(
                final_url=final_url,
                charset=charset,
                response_size=response_size,
                duration_seconds=duration_seconds,
                redirect_count=redirect_count,
                redirect_chain=redirect_chain,
                outcome=outcome,
                dedupe_key=dedupe_key,
            ),
        )
