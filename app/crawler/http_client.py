"""Async HTTP client (Phase 6.1): one shared `httpx.AsyncClient`, manual validated redirects,
streamed size-capped bodies, content-type gating. `fetch` never raises for per-URL failures."""

from __future__ import annotations

import asyncio
import time
from types import TracebackType
from typing import TYPE_CHECKING, Self
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.logging import get_logger
from app.crawler.models import FetchError, FetchOutcome, PageResult, RedirectHop
from app.urls.content import parse_media_type
from app.urls.engine import UrlEvaluator
from app.urls.errors import UrlRejected
from app.urls.ssrf import Resolver, resolve_and_check, system_resolver

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

logger = get_logger(__name__)

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class HttpClientConfig:
    """Plain configuration for `AsyncHttpClient` (built from `Settings` or given directly)."""

    def __init__(
        self,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 15.0,
        write_timeout: float = 10.0,
        pool_timeout: float = 10.0,
        follow_redirects: bool = True,
        max_redirects: int = 5,
        max_response_bytes: int = 5 * 1024 * 1024,
        supported_content_types: frozenset[str] = frozenset({"text/html", "application/xhtml+xml"}),
        allow_missing_content_type: bool = True,
        user_agent: str = "leadfinder/0.6 (+http-crawler)",
        max_connections: int = 20,
        max_keepalive_connections: int = 10,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self.pool_timeout = pool_timeout
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.supported_content_types = frozenset(t.lower() for t in supported_content_types)
        self.allow_missing_content_type = allow_missing_content_type
        self.user_agent = user_agent
        self.max_connections = max_connections
        self.max_keepalive_connections = max_keepalive_connections

    @classmethod
    def from_settings(cls, s: Settings) -> HttpClientConfig:
        types = frozenset(t.strip().lower() for t in s.crawler_supported_content_types.split(",") if t.strip())
        return cls(
            connect_timeout=s.crawler_connect_timeout,
            read_timeout=s.crawler_read_timeout,
            write_timeout=s.crawler_write_timeout,
            pool_timeout=s.crawler_pool_timeout,
            follow_redirects=s.crawler_follow_redirects,
            max_redirects=s.crawler_max_redirects,
            max_response_bytes=s.crawler_max_response_bytes,
            supported_content_types=types,
            allow_missing_content_type=s.crawler_allow_missing_content_type,
            user_agent=s.crawler_user_agent,
            max_connections=s.crawler_max_connections,
            max_keepalive_connections=s.crawler_max_keepalive_connections,
        )


def _charset(content_type_header: str | None) -> str | None:
    if not content_type_header:
        return None
    for part in content_type_header.split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip("\"'").lower() or None
    return None


class AsyncHttpClient:
    """Shared-client fetcher. Use as `async with AsyncHttpClient(config) as client:`.

    Redirects are followed manually (httpx `follow_redirects=False`) so every hop can be recorded,
    loop-checked, counted and (optionally) re-validated by a `UrlEvaluator` + DNS check.
    """

    def __init__(
        self,
        config: HttpClientConfig | None = None,
        *,
        evaluator: UrlEvaluator | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or HttpClientConfig()
        self._evaluator = evaluator
        self._resolver = resolver
        c = self.config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=c.connect_timeout, read=c.read_timeout,
                write=c.write_timeout, pool=c.pool_timeout,
            ),
            limits=httpx.Limits(
                max_connections=c.max_connections,
                max_keepalive_connections=c.max_keepalive_connections,
            ),
            follow_redirects=False,
            headers={"User-Agent": c.user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> PageResult:
        """Fetch `url`. Always returns a `PageResult`; failures are described in `.outcome`/`.error`."""
        start = time.perf_counter()
        redirects: list[RedirectHop] = []
        try:
            result = await self._fetch(url, redirects)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # last-resort guard: one URL must never crash the crawler
            logger.warning("http_fetch_unexpected_error", extra={"url": url, "error": repr(exc)})
            result = self._failure(url, redirects[-1].location if redirects else url,
                                   FetchOutcome.ERROR, "unexpected error", type(exc).__name__, redirects)
        return _with_duration(result, time.perf_counter() - start)

    # -- internals -----------------------------------------------------------------------

    async def _fetch(self, url: str, redirects: list[RedirectHop]) -> PageResult:
        cfg = self.config
        current = url
        visited = {url}
        while True:
            checked = await self._check_url(current, url, redirects)
            if isinstance(checked, PageResult):
                return checked
            current = checked
            try:
                return_or_hop = await self._request(url, current, redirects)
            except httpx.InvalidURL as exc:
                return self._failure(url, current, FetchOutcome.INVALID_URL, "invalid URL", type(exc).__name__, redirects)
            except httpx.UnsupportedProtocol as exc:
                return self._failure(url, current, FetchOutcome.INVALID_URL, "unsupported protocol", type(exc).__name__, redirects)
            except httpx.TooManyRedirects as exc:  # pragma: no cover - we never let httpx redirect
                return self._failure(url, current, FetchOutcome.REDIRECT_ERROR, "too many redirects", type(exc).__name__, redirects)
            except httpx.TimeoutException as exc:
                return self._failure(url, current, FetchOutcome.TIMEOUT, f"{type(exc).__name__}", type(exc).__name__, redirects)
            except (httpx.TransportError, httpx.StreamError) as exc:
                return self._failure(url, current, FetchOutcome.CONNECTION_ERROR, str(exc)[:200] or type(exc).__name__, type(exc).__name__, redirects)
            except httpx.HTTPError as exc:
                return self._failure(url, current, FetchOutcome.ERROR, str(exc)[:200], type(exc).__name__, redirects)
            if isinstance(return_or_hop, PageResult):
                return return_or_hop
            # A redirect response: (status, location)
            status, location = return_or_hop
            if not cfg.follow_redirects:
                return self._failure(url, current, FetchOutcome.REDIRECT_NOT_FOLLOWED,
                                     f"redirect ({status}) not followed", "", redirects, status_code=status)
            target = urljoin(current, location)
            if urlsplit(target).scheme not in ("http", "https"):
                return self._failure(url, current, FetchOutcome.REDIRECT_ERROR, "redirect to unsupported scheme", "bad_scheme", redirects, status_code=status)
            redirects.append(RedirectHop(current, status, target))
            if len(redirects) > cfg.max_redirects:
                return self._failure(url, target, FetchOutcome.REDIRECT_ERROR, "too many redirects", "max_redirects", redirects, status_code=status)
            if target in visited:
                return self._failure(url, target, FetchOutcome.REDIRECT_ERROR, "redirect loop", "loop", redirects, status_code=status)
            visited.add(target)
            current = target

    async def _check_url(self, current: str, original: str, redirects: list[RedirectHop]) -> str | PageResult:
        """Validate scheme/host (and policy + DNS when an evaluator is configured)."""
        try:
            parts = urlsplit(current)
            host = parts.hostname
            _ = parts.port  # raises ValueError on a bad port
        except ValueError:
            return self._failure(original, current, FetchOutcome.INVALID_URL, "malformed URL", "ValueError", redirects)
        if parts.scheme not in ("http", "https") or not host:
            return self._failure(original, current, FetchOutcome.INVALID_URL,
                                 "URL must be absolute http(s) with a host", "bad_url", redirects)
        if self._evaluator is None:
            return current
        decision = self._evaluator.evaluate(current, check_extension=False)
        if not decision.allowed or decision.url is None:
            return self._failure(original, current, FetchOutcome.BLOCKED, "refused by URL policy",
                                 str(decision.reason), redirects)
        normalized = decision.url
        try:
            await asyncio.to_thread(resolve_and_check, urlsplit(normalized).hostname or "", self._resolver, self._evaluator.policy)
        except UrlRejected as exc:
            return self._failure(original, normalized, FetchOutcome.BLOCKED, "refused by SSRF check", exc.detail, redirects)
        return normalized

    async def _request(
        self, original: str, url: str, redirects: list[RedirectHop]
    ) -> PageResult | tuple[int, str]:
        cfg = self.config
        async with self._client.stream("GET", url) as resp:
            status = resp.status_code
            if status in _REDIRECT_CODES:
                location = resp.headers.get("location")
                if not location:
                    return self._failure(original, url, FetchOutcome.REDIRECT_ERROR, "redirect without Location", "no_location", redirects, status_code=status)
                return status, location
            headers = {k.lower(): v for k, v in resp.headers.items()}
            raw_ct = resp.headers.get("content-type")
            media = parse_media_type(raw_ct)
            base = dict(url=original, final_url=url, status_code=status, content_type=media,
                        charset=_charset(raw_ct), headers=headers, redirects=tuple(redirects))
            declared = resp.headers.get("content-length")
            if declared and declared.strip().isdigit() and int(declared) > cfg.max_response_bytes:
                return PageResult(outcome=FetchOutcome.TOO_LARGE, error=FetchError(FetchOutcome.TOO_LARGE, "declared Content-Length exceeds limit", declared), **base)
            if not (200 <= status < 300):
                # Error pages are not read; only status + headers are reported.
                return PageResult(outcome=FetchOutcome.HTTP_ERROR, error=FetchError(FetchOutcome.HTTP_ERROR, f"HTTP {status}", str(status)), **base)
            if not self._content_type_supported(media):
                return PageResult(outcome=FetchOutcome.UNSUPPORTED_CONTENT_TYPE, error=FetchError(FetchOutcome.UNSUPPORTED_CONTENT_TYPE, f"unsupported content type: {media or 'missing'}", media or ""), **base)
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > cfg.max_response_bytes:
                    return PageResult(outcome=FetchOutcome.TOO_LARGE, size=size, error=FetchError(FetchOutcome.TOO_LARGE, "body exceeds limit", str(cfg.max_response_bytes)), **base)
                chunks.append(chunk)
            return PageResult(outcome=FetchOutcome.OK, size=size, body=b"".join(chunks), **base)

    def _content_type_supported(self, media: str | None) -> bool:
        if media is None:
            return self.config.allow_missing_content_type
        return media in self.config.supported_content_types

    @staticmethod
    def _failure(
        url: str, final_url: str, kind: FetchOutcome, message: str, detail: str,
        redirects: list[RedirectHop], *, status_code: int | None = None,
    ) -> PageResult:
        return PageResult(url=url, final_url=final_url, outcome=kind, status_code=status_code,
                          redirects=tuple(redirects), error=FetchError(kind, message, detail))


def _with_duration(result: PageResult, seconds: float) -> PageResult:
    from dataclasses import replace

    return replace(result, duration=seconds)

