"""Enumerations used by the models. Stored as short strings with a CHECK constraint."""

from enum import StrEnum

import sqlalchemy as sa


class CrawlStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CrawlStopReason(StrEnum):
    """Why a crawl run stopped (mirrors the crawler's StopReason values)."""

    COMPLETED = "completed"
    MAX_PAGES = "max_pages"
    MAX_CRAWL_TIME = "max_crawl_time"
    SEED_REJECTED = "seed_rejected"
    CANCELLED = "cancelled"  # stopped on request (Phase 6.4)


class PageStatus(StrEnum):
    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"
    SKIPPED = "skipped"


class PageOutcome(StrEnum):
    """Fetch outcome / failure kind recorded on a page (mirrors the crawler's FetchOutcome values)."""

    OK = "ok"
    HTTP_ERROR = "http_error"
    REDIRECT_NOT_FOLLOWED = "redirect_not_followed"
    REDIRECT_ERROR = "redirect_error"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    INVALID_URL = "invalid_url"
    BLOCKED = "blocked"
    ERROR = "error"


class EmailEvidenceSource(StrEnum):
    MAILTO_LINK = "mailto_link"
    PAGE_TEXT = "page_text"
    OBFUSCATED_TEXT = "obfuscated_text"
    STRUCTURED_DATA = "structured_data"
    OTHER = "other"


class PersonEvidenceSource(StrEnum):
    TEAM_LISTING = "team_listing"
    BYLINE = "byline"
    STRUCTURED_DATA = "structured_data"
    PAGE_TEXT = "page_text"
    OTHER = "other"


class VerificationStatus(StrEnum):
    UNKNOWN = "unknown"
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    CATCH_ALL = "catch_all"


class VerificationMethod(StrEnum):
    SYNTAX = "syntax"
    DNS_MX = "dns_mx"
    SMTP = "smtp"
    THIRD_PARTY = "third_party"


class EmailPatternType(StrEnum):
    FIRST = "first"
    LAST = "last"
    FIRST_LAST = "first.last"
    FIRSTLAST = "firstlast"
    F_LAST = "f.last"
    FLAST = "flast"
    FIRST_L = "first.l"
    FIRSTL = "firstl"
    LAST_FIRST = "last.first"
    FIRST_UNDERSCORE_LAST = "first_last"
    OTHER = "other"


def enum_type(enum_cls: type[StrEnum]) -> sa.Enum:
    """Portable enum column: VARCHAR + named CHECK constraint, stores the enum *value*."""
    return sa.Enum(
        enum_cls,
        name=enum_cls.__name__.lower(),
        native_enum=False,
        length=32,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )
