"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""

from app.models.api_key import ApiKey
from app.models.crawl import Crawl
from app.models.domain import Domain
from app.models.email import Email, EmailEvidence
from app.models.enums import (
    CrawlStatus,
    CrawlStopReason,
    EmailEvidenceSource,
    EmailPatternType,
    PageOutcome,
    PageStatus,
    PersonEvidenceSource,
    VerificationMethod,
    VerificationStatus,
)
from app.models.lead import Lead
from app.models.page import Page
from app.models.pattern import EmailPattern
from app.models.person import Person, PersonEvidence
from app.models.verification import EmailVerification

__all__ = [
    "ApiKey",
    "Crawl",
    "CrawlStatus",
    "CrawlStopReason",
    "Domain",
    "Email",
    "EmailEvidence",
    "EmailEvidenceSource",
    "EmailPattern",
    "EmailPatternType",
    "EmailVerification",
    "Lead",
    "Page",
    "PageOutcome",
    "PageStatus",
    "Person",
    "PersonEvidence",
    "PersonEvidenceSource",
    "VerificationMethod",
    "VerificationStatus",
]
