"""Discovery pipeline: target -> robots.txt -> Sitemap declarations -> /sitemap.xml fallback
-> sitemap (`<urlset>` -> page URLs, `<sitemapindex>` -> child sitemaps -> page URLs)
-> existing URL policy (`UrlGate`).

Since Phase 5.3 indexes are followed recursively (depth-first, in document order). Safety nets:

* visited set keyed by the project's `dedupe_key` -> each canonical sitemap is fetched at most
  once, which also makes cycles (A -> B -> A) terminate;
* `max_depth` (top-level sitemaps are depth 0) -> deeper children are not fetched and the
  skip is recorded (`depth_limit_reached`, `skipped_by_depth`, `SitemapOutcome.depth_limited`);
* `max_sitemaps` and `max_urls` bound total work: once either budget is spent no further
  sitemap is fetched (top-level sources included).

Recursion depth is bounded by `max_depth` (<= 20), so the Python stack cannot be exhausted.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.discovery.fetcher import Fetcher, FetchResult, FetchStatus
from app.discovery.robots import RobotsResult, parse_robots
from app.discovery.sitemap import (
    SitemapKind,
    SitemapParseResult,
    SitemapParseStatus,
    parse_sitemap,
)
from app.urls.dedupe import SeenUrls
from app.urls.engine import UrlEvaluator, UrlGate
from app.urls.errors import RejectReason, UrlRejected

logger = get_logger(__name__)

_ABSOLUTE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

MAX_DEPTH_CEILING = 20  # hard cap keeps recursion far below Python's stack limit
DEFAULT_MAX_DEPTH = 5  # index nesting: top-level sitemaps are depth 0
DEFAULT_MAX_SITEMAPS = 1000  # sitemap files fetched per discovery run (robots.txt excluded)
DEFAULT_MAX_URLS = 100_000  # page URLs admitted per discovery run


@dataclass(slots=True)
class SitemapOutcome:
    url: str
    fetch_status: FetchStatus
    parse_status: SitemapParseStatus | None = None
    kind: SitemapKind | None = None
    parent: str | None = None  # the index that referenced this sitemap (None = top level)
    locs_found: int = 0  # <loc> values found (pages for a urlset, children for an index)
    pages_admitted: int = 0
    depth: int = 0  # 0 = declared / fallback sitemap, 1 = its children, ...
    depth_limited: bool = False  # an index whose children were not fetched (max_depth reached)
    detail: str = ""  # diagnostic, e.g. "root:html" for an unknown root element
    missing_locs: int = 0
    empty_locs: int = 0
    rejected_children: int = 0  # child <loc> values refused by the URL policy / not absolute
    truncated: bool = False  # file had more <loc> values than the per-file cap; extras ignored


@dataclass(slots=True)
class DiscoveryResult:
    target: str
    robots_url: str | None = None
    robots_status: FetchStatus | None = None
    robots: RobotsResult | None = None
    sitemap_urls: list[str] = field(default_factory=list)  # sitemaps that were attempted
    used_fallback: bool = False
    sitemaps: list[SitemapOutcome] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)  # admitted, normalised, de-duplicated
    rejections: Counter[RejectReason] = field(default_factory=Counter)
    error: str | None = None  # target itself unusable
    sitemap_limit_reached: bool = False  # max sitemap files hit; remaining ones skipped
    skipped_sitemaps: int = 0
    depth_limit_reached: bool = False  # an index sat at max_depth; its children were skipped
    skipped_by_depth: int = 0  # child sitemap references not fetched because of max_depth
    duplicate_sitemap_refs: int = 0  # references to an already-visited sitemap (incl. cycles)
    max_depth_seen: int = 0
    url_limit_reached: bool = False  # max page URLs hit; remaining ones dropped

    @property
    def failed_sitemaps(self) -> list[SitemapOutcome]:
        """Sitemaps that could not be fetched or parsed (isolated; others still processed)."""
        return [
            o for o in self.sitemaps
            if o.fetch_status is not FetchStatus.OK
            or (o.parse_status is not None and o.parse_status is not SitemapParseStatus.OK)
        ]


class SitemapDiscovery:
    """Runs sitemap discovery (Phases 5.1-5.4). `discover()` never raises."""

    def __init__(
        self,
        evaluator: UrlEvaluator,
        fetcher: Fetcher,
        *,
        max_sitemap_bytes: int | None = None,
        max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
        max_urls: int = DEFAULT_MAX_URLS,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        if max_sitemaps < 1 or max_urls < 1:
            raise ValueError("max_sitemaps and max_urls must be >= 1")
        if not 0 <= max_depth <= MAX_DEPTH_CEILING:
            raise ValueError(f"max_depth must be within 0-{MAX_DEPTH_CEILING}")
        self._evaluator = evaluator
        self._fetch = fetcher
        self._max_bytes = max_sitemap_bytes
        self._max_sitemaps = max_sitemaps
        self._max_urls = max_urls
        self._max_depth = max_depth

    def discover(self, target: str) -> DiscoveryResult:
        result = DiscoveryResult(target=target)
        try:
            origin = self._origin(target)
        except UrlRejected as exc:
            result.error = str(exc)
            return result
        run = _Run(result, UrlGate(self._evaluator), SeenUrls(self._evaluator.policy))

        result.robots_url = f"{origin}/robots.txt"
        robots_fetch = self._safe_fetch(result.robots_url)
        result.robots_status = robots_fetch.status
        if robots_fetch.ok:
            result.robots = parse_robots(robots_fetch.body)
        declared = self._declared_sitemaps(result.robots, result)

        candidates = declared
        if not candidates:  # robots.txt missing, failed, empty, or without Sitemap lines
            result.used_fallback = True
            candidates = [f"{origin}/sitemap.xml"]
        result.sitemap_urls = list(candidates)

        for sitemap_url in candidates:  # every top-level source is independent
            self._process(run, sitemap_url, parent=None, depth=0)
        gate = run.gate
        result.rejections.update(gate.rejections)  # keeps declaration-stage rejections
        logger.info(
            "sitemap_discovery_done",
            extra={
                "target": origin, "robots_status": str(result.robots_status),
                "sitemaps": len(result.sitemaps), "urls": len(result.urls),
                "fallback": result.used_fallback,
            },
        )
        return result

    # -- helpers -------------------------------------------------------------------------
    def _origin(self, target: str) -> str:
        n = self._evaluator.normalize(target if "//" in target else f"https://{target}")
        decision = self._evaluator.evaluate_normalized(n, check_extension=False)
        if not decision.allowed:
            assert decision.reason is not None
            raise UrlRejected(decision.reason, decision.detail)
        host = n.host
        port = f":{n.port}" if n.port is not None else ""
        return f"{n.scheme}://{host}{port}"

    def _safe_fetch(self, url: str) -> FetchResult:
        try:
            return self._fetch(url)
        except Exception as exc:  # a misbehaving fetcher must not crash discovery
            logger.warning("fetcher_raised", extra={"url": url, "error": repr(exc)})
            return FetchResult(url, FetchStatus.CONNECTION_ERROR, detail=type(exc).__name__)

    def _declared_sitemaps(self, robots: RobotsResult | None, result: DiscoveryResult) -> list[str]:
        """Declared Sitemap URLs that pass the URL policy, de-duplicated after normalisation."""
        if robots is None:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for raw in robots.sitemaps:
            decision = self._evaluator.evaluate(raw, check_extension=False)  # XML, not a page
            if not decision.allowed or decision.url is None:
                if decision.reason is not None:
                    result.rejections[decision.reason] += 1
                continue
            if decision.url not in seen:
                seen.add(decision.url)
                out.append(decision.url)
        return out

    def _process(self, run: _Run, url: str, parent: str | None, depth: int) -> None:
        """Fetch one sitemap: add its pages (urlset) or recurse into its children (index)."""
        result = run.result
        if result.url_limit_reached or len(result.urls) >= self._max_urls:
            result.url_limit_reached = True  # page budget exhausted: fetching more cannot admit more
            return
        if not run.sitemaps_seen.add(url):  # canonical visited set: duplicates and cycles stop here
            result.duplicate_sitemap_refs += 1
            logger.info("sitemap_already_visited", extra={"url": url, "parent": parent})
            return
        if len(result.sitemaps) >= self._max_sitemaps:
            result.sitemap_limit_reached = True
            result.skipped_sitemaps += 1
            return
        outcome, parsed = self._load_sitemap(url, parent)
        outcome.depth = depth
        result.sitemaps.append(outcome)
        result.max_depth_seen = max(result.max_depth_seen, depth)
        if parsed is None:
            return
        outcome.locs_found = len(parsed.locs)
        outcome.missing_locs, outcome.empty_locs = parsed.missing_locs, parsed.empty_locs
        outcome.truncated = parsed.truncated
        if parsed.kind is SitemapKind.INDEX:
            self._follow_index(run, url, parsed, outcome, depth)
            return
        for loc in parsed.locs:
            if len(result.urls) >= self._max_urls:
                result.url_limit_reached = True
                break
            decision = run.gate.admit(loc, base=url)
            if decision.allowed and decision.url:
                result.urls.append(decision.url)
                outcome.pages_admitted += 1

    def _follow_index(
        self, run: _Run, url: str, parsed: SitemapParseResult, outcome: SitemapOutcome, depth: int
    ) -> None:
        result = run.result
        for position, loc in enumerate(parsed.locs):
            if result.url_limit_reached:
                break  # page budget exhausted: nothing more can be admitted
            if result.sitemap_limit_reached:  # file budget exhausted: count what is left unvisited
                result.skipped_sitemaps += len(parsed.locs) - position
                break
            if not _ABSOLUTE.match(loc):  # the sitemap protocol requires absolute URLs
                result.rejections[RejectReason.INVALID_URL] += 1
                outcome.rejected_children += 1
                continue
            decision = self._evaluator.evaluate(loc, check_extension=False)
            if not decision.allowed or decision.url is None:
                if decision.reason is not None:
                    result.rejections[decision.reason] += 1
                outcome.rejected_children += 1
                continue
            if depth + 1 > self._max_depth:
                outcome.depth_limited = True
                result.depth_limit_reached = True
                result.skipped_by_depth += 1
                logger.info(
                    "sitemap_depth_limit",
                    extra={"url": decision.url, "parent": url, "max_depth": self._max_depth},
                )
                continue
            self._process(run, decision.url, parent=url, depth=depth + 1)

    def _load_sitemap(
        self, url: str, parent: str | None = None
    ) -> tuple[SitemapOutcome, SitemapParseResult | None]:
        fetched = self._safe_fetch(url)
        outcome = SitemapOutcome(url, fetched.status, parent=parent)
        if not fetched.ok:
            logger.info("sitemap_fetch_failed", extra={"url": url, "status": fetched.status.value})
            return outcome, None
        kwargs = {} if self._max_bytes is None else {"max_bytes": self._max_bytes}
        parsed = parse_sitemap(fetched.body, **kwargs)
        outcome.parse_status = parsed.status
        outcome.kind = parsed.kind
        outcome.detail = parsed.detail
        if parsed.status is not SitemapParseStatus.OK:
            logger.info("sitemap_not_parsed", extra={"url": url, "status": parsed.status.value})
            return outcome, None
        return outcome, parsed


@dataclass(slots=True)
class _Run:
    """Per-discovery mutable state."""

    result: DiscoveryResult
    gate: UrlGate
    sitemaps_seen: SeenUrls


__all__ = ["DiscoveryResult", "SitemapDiscovery", "SitemapOutcome"]
