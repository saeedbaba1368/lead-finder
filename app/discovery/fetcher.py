"""Minimal, safe HTTP fetch used by discovery.

`Fetcher` is a plain callable (`url -> FetchResult`) so tests inject fakes. `HttpFetcher` is the
stdlib implementation: it never raises for network problems, caps the body size, follows a
small number of redirects and validates every hop (and the DNS answer) with the URL policy.
"""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.urls.engine import UrlEvaluator
from app.urls.errors import UrlRejected
from app.urls.ssrf import Resolver, resolve_and_check, system_resolver

logger = get_logger(__name__)


class FetchStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"  # 404 / 410
    FORBIDDEN = "forbidden"  # 401 / 403
    RATE_LIMITED = "rate_limited"  # 429 (not retried: discovery never retries or hammers a host)
    SERVER_ERROR = "server_error"  # 5xx
    HTTP_ERROR = "http_error"  # any other non-200 status
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    TOO_LARGE = "too_large"
    BLOCKED = "blocked"  # refused by the URL policy / SSRF checks
    TOO_MANY_REDIRECTS = "too_many_redirects"


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status: FetchStatus
    http_status: int | None = None
    body: bytes = b""
    content_type: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is FetchStatus.OK


Fetcher = Callable[[str], FetchResult]


def status_for_http_code(code: int) -> FetchStatus:
    if code == 200:
        return FetchStatus.OK
    if code in (404, 410):
        return FetchStatus.NOT_FOUND
    if code in (401, 403):
        return FetchStatus.FORBIDDEN
    if code == 429:
        return FetchStatus.RATE_LIMITED
    if 500 <= code <= 599:
        return FetchStatus.SERVER_ERROR
    return FetchStatus.HTTP_ERROR


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None  # surface 3xx as HTTPError so each hop can be validated


class HttpFetcher:
    """Fetch a URL with a timeout, a body-size cap and validated redirects. Never raises."""

    def __init__(
        self,
        evaluator: UrlEvaluator,
        *,
        timeout: float = 10.0,
        max_bytes: int = 5 * 1024 * 1024,
        max_redirects: int = 3,
        user_agent: str = "leadfinder/0.5 (+sitemap-discovery)",
        resolver: Resolver = system_resolver,
    ) -> None:
        self._evaluator = evaluator
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._resolver = resolver
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __call__(self, url: str) -> FetchResult:
        try:
            return self._fetch(url)
        except Exception as exc:  # last-resort guard: discovery must never crash
            logger.warning("fetch_unexpected_error", extra={"url": url, "error": repr(exc)})
            return FetchResult(url, FetchStatus.CONNECTION_ERROR, detail=type(exc).__name__)

    def _fetch(self, url: str) -> FetchResult:
        current = url
        for _ in range(self._max_redirects + 1):
            decision = self._evaluator.evaluate(current, check_extension=False)
            if not decision.allowed or decision.url is None:
                return FetchResult(current, FetchStatus.BLOCKED, detail=str(decision.reason))
            current = decision.url
            host = urlsplit(current).hostname or ""
            try:
                resolve_and_check(host, self._resolver, self._evaluator.policy)
            except UrlRejected as exc:
                return FetchResult(current, FetchStatus.BLOCKED, detail=exc.detail)
            location = self._request(current)
            if isinstance(location, FetchResult):
                return location
            redirected = self._evaluator.evaluate_redirect(
                current, location, check_extension=False
            )
            if not redirected.allowed or redirected.url is None:
                return FetchResult(current, FetchStatus.BLOCKED, detail="redirect_blocked")
            current = redirected.url
        return FetchResult(url, FetchStatus.TOO_MANY_REDIRECTS)

    def _request(self, url: str) -> FetchResult | str:
        """Return a FetchResult, or the `Location` value of a redirect."""
        req = urllib.request.Request(  # scheme validated by the URL policy
            url,
            headers={"User-Agent": self._user_agent, "Accept": "text/plain, text/xml, */*;q=0.5"},
        )
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > self._max_bytes:
                    return FetchResult(url, FetchStatus.TOO_LARGE, resp.status)
                body = resp.read(self._max_bytes + 1)
                if len(body) > self._max_bytes:
                    return FetchResult(url, FetchStatus.TOO_LARGE, resp.status)
                return FetchResult(
                    url, status_for_http_code(resp.status), resp.status, body,
                    resp.headers.get("Content-Type"),
                )
        except urllib.error.HTTPError as err:
            try:
                if err.code in (301, 302, 303, 307, 308):
                    location = err.headers.get("Location")
                    if location:
                        return location
                return FetchResult(url, status_for_http_code(err.code), err.code)
            finally:
                err.close()
        except TimeoutError:
            return FetchResult(url, FetchStatus.TIMEOUT)
        except urllib.error.URLError as err:
            if isinstance(err.reason, (TimeoutError, socket.timeout)):
                return FetchResult(url, FetchStatus.TIMEOUT)
            return FetchResult(url, FetchStatus.CONNECTION_ERROR, detail=str(err.reason)[:200])
        except (http.client.HTTPException, OSError, ValueError) as err:
            return FetchResult(url, FetchStatus.CONNECTION_ERROR, detail=type(err).__name__)
