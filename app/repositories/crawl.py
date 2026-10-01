from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db.base import utcnow
from app.models import Crawl, CrawlStatus
from app.repositories.base import BaseRepository
from app.repositories.errors import InvalidTransitionError

_ALLOWED: dict[CrawlStatus, set[CrawlStatus]] = {
    CrawlStatus.PENDING: {CrawlStatus.RUNNING, CrawlStatus.FAILED, CrawlStatus.CANCELLED},
    CrawlStatus.RUNNING: {CrawlStatus.COMPLETED, CrawlStatus.FAILED, CrawlStatus.CANCELLED},
    CrawlStatus.COMPLETED: set(),
    CrawlStatus.FAILED: set(),
    CrawlStatus.CANCELLED: set(),
}
_TERMINAL = {CrawlStatus.COMPLETED, CrawlStatus.FAILED, CrawlStatus.CANCELLED}


class CrawlRepository(BaseRepository[Crawl]):
    model = Crawl

    def create(
        self,
        domain_id: int,
        *,
        seed_url: str | None = None,
        max_pages: int = 100,
        max_depth: int = 2,
        api_key_id: int | None = None,
    ) -> Crawl:
        return self.add(
            Crawl(
                domain_id=domain_id,
                seed_url=seed_url,
                max_pages=max_pages,
                max_depth=max_depth,
                api_key_id=api_key_id,
            )
        )

    def get_with_pages(self, id: int) -> Crawl | None:
        return self.get(id, options=[selectinload(Crawl.pages)])

    def list_for_domain(
        self, domain_id: int, *, status: CrawlStatus | None = None, limit: int = 100, offset: int = 0
    ) -> list[Crawl]:
        stmt = select(Crawl).where(Crawl.domain_id == domain_id)
        if status is not None:
            stmt = stmt.where(Crawl.status == status)
        return list(self.session.scalars(stmt.order_by(Crawl.id).limit(limit).offset(offset)))

    def list_by_status(self, status: CrawlStatus, *, limit: int = 100) -> list[Crawl]:
        stmt = select(Crawl).where(Crawl.status == status).order_by(Crawl.created_at, Crawl.id).limit(limit)
        return list(self.session.scalars(stmt))

    def count_by_status(self) -> dict[CrawlStatus, int]:
        rows = self.session.execute(select(Crawl.status, func.count()).group_by(Crawl.status)).all()
        return {status: n for status, n in rows}

    def set_status(
        self, crawl: Crawl, status: CrawlStatus, *, error_message: str | None = None, now: datetime | None = None
    ) -> Crawl:
        """Validated status change. Sets started_at/finished_at; COMPLETED also stamps the domain."""
        if status not in _ALLOWED[crawl.status]:
            raise InvalidTransitionError(f"crawl {crawl.id}: {crawl.status.value} -> {status.value} not allowed")
        now = now or utcnow()
        values: dict = {"status": status}
        if status is CrawlStatus.RUNNING and crawl.started_at is None:
            values["started_at"] = now
        if status in _TERMINAL:
            values["finished_at"] = now
            if crawl.started_at is None:  # e.g. cancelled before it ever ran
                values["started_at"] = now
        if error_message is not None:
            values["error_message"] = error_message
        self.update(crawl, **values)
        if status is CrawlStatus.COMPLETED:
            crawl.domain.last_crawled_at = now
            self.session.flush()
        return crawl

    def increment_pages_crawled(self, crawl: Crawl, by: int = 1) -> Crawl:
        return self.update(crawl, pages_crawled=crawl.pages_crawled + by)
