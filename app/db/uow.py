"""Unit of work: one session + all repositories, with explicit transaction handling.

    with UnitOfWork() as uow:
        domain, _ = uow.domains.get_or_create("example.com")
        uow.crawls.create(domain.id)
    # committed here; if the block raised, everything was rolled back

Pass ``session=`` to reuse an existing session (it is then neither committed nor closed
on exit unless you call ``commit()`` yourself).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_session_factory
from app.repositories import (
    ApiKeyRepository,
    CrawlRepository,
    DomainRepository,
    EmailPatternRepository,
    EmailRepository,
    EmailVerificationRepository,
    PageRepository,
    PersonRepository,
)


class UnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session] | None = None, *, session: Session | None = None):
        self._factory = session_factory
        self._external = session
        self.session: Session = session  # type: ignore[assignment]

    def __enter__(self) -> "UnitOfWork":
        if self._external is None:
            self.session = (self._factory or get_session_factory())()
        s = self.session
        self.domains = DomainRepository(s)
        self.crawls = CrawlRepository(s)
        self.pages = PageRepository(s)
        self.emails = EmailRepository(s)
        self.people = PersonRepository(s)
        self.verifications = EmailVerificationRepository(s)
        self.patterns = EmailPatternRepository(s)
        self.api_keys = ApiKeyRepository(s)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        owned = self._external is None
        try:
            if exc_type is not None:
                self.session.rollback()
            elif owned:
                self.session.commit()
        except BaseException:
            self.session.rollback()
            raise
        finally:
            if owned:
                self.session.close()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Nested transaction: on error only the work inside the block is undone."""
        with self.session.begin_nested():
            yield


def get_uow() -> Iterator[UnitOfWork]:
    """FastAPI dependency: commit on success, roll back if the request raises."""
    with UnitOfWork() as uow:
        yield uow
