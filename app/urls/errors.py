"""Rejection reasons and the exception raised by URL normalisation."""

from enum import StrEnum


class RejectReason(StrEnum):
    # Structural
    EMPTY = "empty"
    TOO_LONG = "too_long"
    INVALID_URL = "invalid_url"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    CREDENTIALS_IN_URL = "credentials_in_url"
    INVALID_HOST = "invalid_host"
    INVALID_PORT = "invalid_port"
    PORT_NOT_ALLOWED = "port_not_allowed"
    PUBLIC_SUFFIX = "public_suffix"
    # Safety
    SSRF_BLOCKED = "ssrf_blocked"
    # Scope
    BLOCKED_DOMAIN = "blocked_domain"
    OUT_OF_SCOPE = "out_of_scope"
    SUBDOMAIN_NOT_RELEVANT = "subdomain_not_relevant"
    # Content eligibility
    NON_HTML_EXTENSION = "non_html_extension"
    CONTENT_TYPE_NOT_ELIGIBLE = "content_type_not_eligible"
    CONTENT_TOO_LARGE = "content_too_large"
    # Traps
    TRAP_DEPTH = "trap_depth"
    TRAP_REPEATED_SEGMENTS = "trap_repeated_segments"
    TRAP_QUERY_PARAMS = "trap_query_params"
    TRAP_PAGINATION = "trap_pagination"
    TRAP_CALENDAR = "trap_calendar"
    TRAP_QUERY_VARIANTS = "trap_query_variants"
    # State
    DUPLICATE = "duplicate"


class UrlRejected(ValueError):
    """Raised when a URL cannot be normalised or fails a hard safety check."""

    def __init__(self, reason: RejectReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
