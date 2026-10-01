"""The URL policy engine: composes normalisation, SSRF, scope, eligibility and trap checks.

`UrlEvaluator` is stateless (safe to share). `UrlGate` adds per-crawl memory (duplicates,
trap counters) and is what the future crawler's frontier will call. Nothing here performs
network access.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

from app.core.logging import get_logger
from app.urls.canonical import CanonicalResult, resolve_canonical
from app.urls.content import check_extension as check_extension_policy
from app.urls.dedupe import SeenUrls
from app.urls.domains import check_scope, registered_domain
from app.urls.errors import RejectReason, UrlRejected
from app.urls.normalize import NormalizedUrl, normalize_url
from app.urls.policy import DEFAULT_POLICY, UrlPolicy
from app.urls.ssrf import host_block_reason
from app.urls.traps import Clock, TrapTracker, detect_stateless_trap

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UrlDecision:
    allowed: bool
    raw: str
    url: str | None = None  # normalised URL (set whenever normalisation succeeded)
    reason: RejectReason | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class UrlEvaluator:
    """Stateless URL checks for a crawl rooted at `seed_url` (scope is skipped without one)."""

    def __init__(
        self,
        policy: UrlPolicy = DEFAULT_POLICY,
        seed_url: str | None = None,
        clock: Clock = date.today,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self.seed: NormalizedUrl | None = (
            normalize_url(seed_url, policy=policy, default_scheme="https") if seed_url else None
        )
        if self.seed is not None:
            reason = host_block_reason(self.seed.host, policy)
            if reason is not None:
                raise UrlRejected(RejectReason.SSRF_BLOCKED, f"seed: {reason}")

    def normalize(self, raw: str, base: str | None = None) -> NormalizedUrl:
        return normalize_url(raw, base=base, policy=self.policy)

    def evaluate(
        self, raw: str, base: str | None = None, *, check_extension: bool = True
    ) -> UrlDecision:
        """Run every stateless check. `base` resolves relative links.

        `check_extension=False` skips only the page-content extension check; it exists for
        fetching crawl-metadata resources (robots.txt, sitemap XML) that are never pages.
        """
        try:
            n = normalize_url(raw, base=base, policy=self.policy)
        except UrlRejected as exc:
            return self._reject(raw, None, exc.reason, exc.detail)
        return self.evaluate_normalized(n, raw, check_extension=check_extension)

    def evaluate_redirect(
        self, from_url: str, location: str, *, check_extension: bool = True
    ) -> UrlDecision:
        """Check a redirect `Location` header. Every hop must pass exactly like a new link,
        so a public URL cannot bounce the fetcher to an internal address or out of scope."""
        return self.evaluate(location, base=from_url, check_extension=check_extension)

    def evaluate_normalized(
        self, n: NormalizedUrl, raw: str | None = None, *, check_extension: bool = True
    ) -> UrlDecision:
        raw = n.url if raw is None else raw
        blocked = host_block_reason(n.host, self.policy)
        if blocked is not None:
            return self._reject(raw, n.url, RejectReason.SSRF_BLOCKED, blocked)

        if registered_domain(n.host, self.policy.extra_public_suffixes) is None:
            return self._reject(raw, n.url, RejectReason.PUBLIC_SUFFIX, n.host)

        if self.seed is not None:
            scope = check_scope(n.host, self.seed.host, self.policy)
        else:
            scope = check_scope(n.host, n.host, self.policy)  # blocked-domain check only
        if not scope.allowed:
            assert scope.reason is not None
            return self._reject(raw, n.url, scope.reason, scope.detail)

        ext = check_extension_policy(n.path, self.policy) if check_extension else None
        if ext is not None:
            return self._reject(raw, n.url, RejectReason.NON_HTML_EXTENSION, ext)

        trap = detect_stateless_trap(n, self.policy, self._clock())
        if trap is not None:
            return self._reject(raw, n.url, trap[0], trap[1])
        return UrlDecision(True, raw, n.url)

    def resolve_canonical(self, page_url: str, href: str | None) -> CanonicalResult:
        return resolve_canonical(page_url, href, self.policy)

    @staticmethod
    def _reject(raw: str, url: str | None, reason: RejectReason, detail: str) -> UrlDecision:
        logger.debug(
            "url_rejected", extra={"url": raw[:200], "reason": reason.value, "detail": detail}
        )
        return UrlDecision(False, raw, url, reason, detail)


class UrlGate:
    """Per-crawl admission control: evaluator + duplicate detection + trap counters."""

    def __init__(self, evaluator: UrlEvaluator) -> None:
        self.evaluator = evaluator
        self.seen = SeenUrls(evaluator.policy)
        self.traps = TrapTracker(evaluator.policy)
        self.rejections: Counter[RejectReason] = Counter()
        self.admitted = 0

    def admit(self, raw: str, base: str | None = None) -> UrlDecision:
        """Decide whether a discovered link should enter the crawl frontier.

        Admitted URLs are remembered, so the same page is admitted only once.
        """
        decision = self.evaluator.evaluate(raw, base)
        if not decision.allowed:
            self.rejections[decision.reason] += 1  # type: ignore[index]
            return decision
        assert decision.url is not None
        n = self.evaluator.normalize(decision.url)

        if decision.url in self.seen:
            return self._count(UrlDecision(False, raw, decision.url, RejectReason.DUPLICATE))
        trap = self.traps.admit(n)
        if trap is not None:
            return self._count(UrlDecision(False, raw, decision.url, trap[0], trap[1]))
        self.seen.add(decision.url)
        self.admitted += 1
        return decision

    def record_canonical(self, page_url: str, href: str | None) -> CanonicalResult:
        """Validate a page's canonical hint and register it for duplicate detection.

        If the canonical URL was already known as another page, the result carries
        `reason="duplicate_of_seen"` and `url` set to that canonical URL.
        """
        result = self.evaluator.resolve_canonical(page_url, href)
        if result.url is None or not result.changed:
            return result
        if self.seen.register_canonical(page_url, result.url):
            return CanonicalResult(result.url, "duplicate_of_seen", True)
        return result

    def record_empty_page(self, url: str) -> None:
        """Tell the trap tracker that this (absolute) paginated URL had no new content."""
        self.traps.record_empty_page(self.evaluator.normalize(url))

    def _count(self, decision: UrlDecision) -> UrlDecision:
        assert decision.reason is not None
        self.rejections[decision.reason] += 1
        return decision

