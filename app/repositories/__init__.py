"""Repository (data-access) layer. Repositories flush; the caller or UnitOfWork commits."""

from app.repositories.api_key import ApiKeyRepository
from app.repositories.base import BaseRepository
from app.repositories.crawl import CrawlRepository
from app.repositories.domain import DomainRepository
from app.repositories.email import EmailRepository
from app.repositories.errors import InvalidTransitionError, NotFoundError, RepositoryError
from app.repositories.lead import LeadRepository, SaveResult
from app.repositories.page import PageRepository
from app.repositories.pattern import EmailPatternRepository
from app.repositories.person import PersonRepository
from app.repositories.verification import EmailVerificationRepository

__all__ = [
    "ApiKeyRepository",
    "BaseRepository",
    "CrawlRepository",
    "DomainRepository",
    "EmailPatternRepository",
    "EmailRepository",
    "EmailVerificationRepository",
    "InvalidTransitionError",
    "LeadRepository",
    "NotFoundError",
    "PageRepository",
    "PersonRepository",
    "RepositoryError",
    "SaveResult",
]
