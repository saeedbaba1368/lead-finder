from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.base import utcnow
from app.models import Email, EmailEvidence, EmailEvidenceSource
from app.repositories.base import BaseRepository


class EmailRepository(BaseRepository[Email]):
    model = Email

    def get_by_address(self, domain_id: int, address: str) -> Email | None:
        stmt = select(Email).where(Email.domain_id == domain_id, Email.address == address.strip().lower())
        return self.session.scalars(stmt).first()

    def find_by_address(self, address: str) -> list[Email]:
        """All rows for an address across domains (uses ``ix_emails_address``)."""
        stmt = select(Email).where(Email.address == address.strip().lower()).order_by(Email.id)
        return list(self.session.scalars(stmt))

    def get_or_create(self, domain_id: int, address: str, *, is_role_based: bool = False) -> tuple[Email, bool]:
        existing = self.get_by_address(domain_id, address)
        if existing is not None:
            return existing, False
        try:
            with self.session.begin_nested():
                email = Email(domain_id=domain_id, address=address, is_role_based=is_role_based)
                self.session.add(email)
                self.session.flush()
            return email, True
        except IntegrityError:
            existing = self.get_by_address(domain_id, address)
            if existing is None:
                raise
            return existing, False

    def get_full(self, id: int) -> Email | None:
        """Email with evidence, verifications and people eagerly loaded."""
        return self.get(
            id,
            options=[
                selectinload(Email.evidence),
                selectinload(Email.verifications),
                selectinload(Email.people),
            ],
        )

    def list_for_domain(
        self,
        domain_id: int,
        *,
        role_based: bool | None = None,
        search: str | None = None,
        with_evidence: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Email]:
        stmt = select(Email).where(Email.domain_id == domain_id)
        if role_based is not None:
            stmt = stmt.where(Email.is_role_based == role_based)
        if search:
            stmt = stmt.where(Email.address.contains(search.strip().lower(), autoescape=True))
        if with_evidence:
            stmt = stmt.options(selectinload(Email.evidence))
        return list(self.session.scalars(stmt.order_by(Email.id).limit(limit).offset(offset)))

    def touch_seen(self, email: Email, when: datetime | None = None) -> Email:
        return self.update(email, last_seen_at=when or utcnow())

    # -- evidence ------------------------------------------------------
    def add_evidence(
        self,
        email_id: int,
        page_id: int,
        *,
        source_type: EmailEvidenceSource = EmailEvidenceSource.PAGE_TEXT,
        snippet: str | None = None,
        confidence: float = 1.0,
    ) -> tuple[EmailEvidence, bool]:
        """Upsert on ``(email, page, source_type)``: a repeat bumps ``occurrences`` and keeps max confidence."""
        stmt = select(EmailEvidence).where(
            EmailEvidence.email_id == email_id,
            EmailEvidence.page_id == page_id,
            EmailEvidence.source_type == source_type,
        )
        existing = self.session.scalars(stmt).first()
        if existing is not None:
            existing.occurrences += 1
            existing.confidence = max(existing.confidence, confidence)
            if existing.snippet is None:
                existing.snippet = snippet
            self.session.flush()
            return existing, False
        evidence = EmailEvidence(
            email_id=email_id, page_id=page_id, source_type=source_type, snippet=snippet, confidence=confidence
        )
        self.session.add(evidence)
        self.session.flush()
        return evidence, True

    def list_evidence(self, email_id: int) -> list[EmailEvidence]:
        stmt = select(EmailEvidence).where(EmailEvidence.email_id == email_id).order_by(EmailEvidence.id)
        return list(self.session.scalars(stmt))
