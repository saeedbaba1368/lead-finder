from sqlalchemy import select

from app.models import EmailPattern, EmailPatternType
from app.repositories.base import BaseRepository


class EmailPatternRepository(BaseRepository[EmailPattern]):
    model = EmailPattern

    def get_for_domain(self, domain_id: int, pattern: EmailPatternType) -> EmailPattern | None:
        stmt = select(EmailPattern).where(EmailPattern.domain_id == domain_id, EmailPattern.pattern == pattern)
        return self.session.scalars(stmt).first()

    def upsert(
        self, domain_id: int, pattern: EmailPatternType, *, sample_count: int, confidence: float
    ) -> tuple[EmailPattern, bool]:
        """Create or overwrite the stats for ``(domain, pattern)``."""
        existing = self.get_for_domain(domain_id, pattern)
        if existing is not None:
            self.update(existing, sample_count=sample_count, confidence=confidence)
            return existing, False
        created = self.add(
            EmailPattern(domain_id=domain_id, pattern=pattern, sample_count=sample_count, confidence=confidence)
        )
        return created, True

    def list_for_domain(self, domain_id: int) -> list[EmailPattern]:
        """Most confident first."""
        stmt = (
            select(EmailPattern)
            .where(EmailPattern.domain_id == domain_id)
            .order_by(EmailPattern.confidence.desc(), EmailPattern.id)
        )
        return list(self.session.scalars(stmt))
