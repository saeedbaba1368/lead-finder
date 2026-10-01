"""Result model for the async HTTP client (Phase 6.1). Plain data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FetchOutcome(StrEnum):
    OK = "ok"  # 2xx and the content type is supported
    HTTP_ERROR = "http_error"  # 4xx / 5xx / unexpected status
    REDIRECT_NOT_FOLLOWED = "redirect_not_followed"  # 3xx while follow_redirects is off
    REDIRECT_ERROR = "redirect_error"  # loop, too many hops, bad/missing Location
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    INVALID_URL = "invalid_url"
    BLOCKED = "blocked"  # refused by the URL policy / SSRF checks
    ERROR = "error"  # any other unexpected failure (never raised to the caller)


@dataclass(frozen=True, slots=True)
class RedirectHop:
    url: str
    status_code: int
    location: str  # resolved absolute target


@dataclass(frozen=True, slots=True)
class FetchError:
    kind: FetchOutcome
    message: str
    detail: str = ""  # exception class name or policy reason


@dataclass(frozen=True, slots=True)
class PageResult:
    url: str
    final_url: str
    outcome: FetchOutcome
    status_code: int | None = None
    content_type: str | None = None  # media type only, lower-case, e.g. "text/html"
    charset: str | None = None
    headers: dict[str, str] = field(default_factory=dict)  # lower-case names
    size: int = 0  # bytes actually read
    body: bytes = b""
    redirects: tuple[RedirectHop, ...] = ()
    duration: float = 0.0  # seconds, whole fetch including redirects
    error: FetchError | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is FetchOutcome.OK

    @property
    def redirected(self) -> bool:
        return bool(self.redirects)

    @property
    def text(self) -> str:
        """Body decoded with the declared charset (fallback UTF-8, undecodable bytes replaced)."""
        try:
            return self.body.decode(self.charset or "utf-8", errors="replace")
        except LookupError:  # unknown charset label
            return self.body.decode("utf-8", errors="replace")
