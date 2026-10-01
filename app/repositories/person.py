from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import Email, Person, PersonEvidence, PersonEvidenceSource
from app.repositories.base import BaseRepository
from app.repositories.errors import NotFoundError


class PersonRepository(BaseRepository[Person]):
    model = Person

    def create(
        self,
        domain_id: int,
        full_name: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        job_title: str | None = None,
        email_id: int | None = None,
        confidence: float = 0.0,
    ) -> Person:
        return self.add(
            Person(
                domain_id=domain_id,
                full_name=full_name.strip(),
                first_name=first_name,
                last_name=last_name,
                job_title=job_title,
                email_id=email_id,
                confidence=confidence,
            )
        )

    def get_full(self, id: int) -> Person | None:
        """Person with email and evidence eagerly loaded."""
        return self.get(id, options=[selectinload(Person.email), selectinload(Person.evidence)])

    def find_by_name(self, domain_id: int, full_name: str) -> list[Person]:
        stmt = (
            select(Person)
            .where(Person.domain_id == domain_id, func.lower(Person.full_name) == full_name.strip().lower())
            .order_by(Person.id)
        )
        return list(self.session.scalars(stmt))

    def list_for_domain(
        self, domain_id: int, *, with_email: bool = False, limit: int = 100, offset: int = 0
    ) -> list[Person]:
        stmt = select(Person).where(Person.domain_id == domain_id)
        if with_email:
            stmt = stmt.options(selectinload(Person.email))
        return list(self.session.scalars(stmt.order_by(Person.id).limit(limit).offset(offset)))

    def link_email(self, person: Person, email_id: int | None) -> Person:
        """Attach (or with ``None`` detach) an email. The email must belong to the person's domain."""
        if email_id is not None:
            email = self.session.get(Email, email_id)
            if email is None:
                raise NotFoundError("Email", email_id)
            if email.domain_id != person.domain_id:
                raise ValueError("email belongs to a different domain than the person")
        return self.update(person, email_id=email_id)

    def add_evidence(
        self,
        person_id: int,
        page_id: int,
        *,
        source_type: PersonEvidenceSource = PersonEvidenceSource.PAGE_TEXT,
        snippet: str | None = None,
        confidence: float = 1.0,
    ) -> tuple[PersonEvidence, bool]:
        """Upsert on ``(person, page, source_type)``; a repeat keeps the higher confidence."""
        stmt = select(PersonEvidence).where(
            PersonEvidence.person_id == person_id,
            PersonEvidence.page_id == page_id,
            PersonEvidence.source_type == source_type,
        )
        existing = self.session.scalars(stmt).first()
        if existing is not None:
            existing.confidence = max(existing.confidence, confidence)
            if existing.snippet is None:
                existing.snippet = snippet
            self.session.flush()
            return existing, False
        evidence = PersonEvidence(
            person_id=person_id, page_id=page_id, source_type=source_type, snippet=snippet, confidence=confidence
        )
        self.session.add(evidence)
        self.session.flush()
        return evidence, True

    def list_evidence(self, person_id: int) -> list[PersonEvidence]:
        stmt = select(PersonEvidence).where(PersonEvidence.person_id == person_id).order_by(PersonEvidence.id)
        return list(self.session.scalars(stmt))
