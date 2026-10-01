from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.base import utcnow
from app.models import Domain
from app.repositories.base import BaseRepository


class DomainRepository(BaseRepository[Domain]):
    model = Domain

    def get_by_name(self, name: str) -> Domain | None:
        return self.session.scalars(select(Domain).where(Domain.name == name.strip().lower())).first()

    def get_or_create(self, name: str) -> tuple[Domain, bool]:
        """Return ``(domain, created)``. Safe against concurrent inserts (savepoint + re-select)."""
        existing = self.get_by_name(name)
        if existing is not None:
            return existing, False
        try:
            with self.session.begin_nested():
                domain = Domain(name=name)
                self.session.add(domain)
                self.session.flush()
            return domain, True
        except IntegrityError:
            existing = self.get_by_name(name)
            if existing is None:
                raise
            return existing, False

    def search(self, text: str | None = None, *, limit: int = 100, offset: int = 0) -> list[Domain]:
        stmt = select(Domain).order_by(Domain.id).limit(limit).offset(offset)
        if text:
            stmt = stmt.where(Domain.name.contains(text.strip().lower(), autoescape=True))
        return list(self.session.scalars(stmt))

    def get_with_relations(self, id: int) -> Domain | None:
        """Domain with crawls, emails, people and patterns eagerly loaded (selectin)."""
        return self.get(
            id,
            options=[
                selectinload(Domain.crawls),
                selectinload(Domain.emails),
                selectinload(Domain.people),
                selectinload(Domain.email_patterns),
            ],
        )

    def mark_crawled(self, domain: Domain, when: datetime | None = None) -> Domain:
        return self.update(domain, last_crawled_at=when or utcnow())
