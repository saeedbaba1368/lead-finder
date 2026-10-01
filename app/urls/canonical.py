"""Canonical-URL handling (`<link rel="canonical">`), with defences against bad hints."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from app.urls.domains import registered_domain, strip_www
from app.urls.errors import RejectReason, UrlRejected
from app.urls.normalize import normalize_url
from app.urls.policy import DEFAULT_POLICY, UrlPolicy
from app.urls.ssrf import host_block_reason
from app.urls.traps import pagination_info

_MAX_HTML_BYTES = 256 * 1024


class _CanonicalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href: str | None = None
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag == "body":
            self.done = True
        elif tag == "link" and self.href is None:
            values = {k.lower(): (v or "") for k, v in attrs}
            if "canonical" in values.get("rel", "").lower().split() and values.get("href", "").strip():
                self.href = values["href"].strip()
                self.done = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.done = True


def extract_canonical_href(html: str) -> str | None:
    """Return the raw href of the first `<link rel="canonical">` in the document head."""
    parser = _CanonicalParser()
    try:
        for start in range(0, min(len(html), _MAX_HTML_BYTES), 8192):
            parser.feed(html[start : min(start + 8192, _MAX_HTML_BYTES)])
            if parser.done:
                break
    except Exception:  # noqa: BLE001 - malformed HTML must never break the pipeline
        return parser.href
    return parser.href


@dataclass(frozen=True, slots=True)
class CanonicalResult:
    url: str | None  # the canonical URL to use (None = ignore the hint)
    reason: str = ""  # why a hint was ignored, or "" when accepted
    changed: bool = False  # canonical differs from the page URL


def resolve_canonical(
    page_url: str, href: str | None, policy: UrlPolicy = DEFAULT_POLICY
) -> CanonicalResult:
    """Validate a canonical hint for `page_url`.

    The hint is ignored when it: is missing/invalid; targets an unsafe host (SSRF); leaves
    the page's registered domain (unless allowed); points a deep page at the site root
    (a very common misconfiguration that would collapse a whole site); or drops the
    pagination of a paginated URL.
    """
    page = normalize_url(page_url, policy=policy)
    if not href or not href.strip():
        return CanonicalResult(None, "missing")
    try:
        canonical = normalize_url(href, base=page.url, policy=policy)
    except UrlRejected as exc:
        return CanonicalResult(None, f"invalid:{exc.reason.value}")

    if host_block_reason(canonical.host, policy) is not None:
        return CanonicalResult(None, RejectReason.SSRF_BLOCKED.value)

    if not policy.canonical_allow_cross_domain:
        extra = policy.extra_public_suffixes
        same_site = (
            strip_www(canonical.host) == strip_www(page.host)
            or registered_domain(canonical.host, extra) == registered_domain(page.host, extra)
        )
        if not same_site:
            return CanonicalResult(None, "cross_domain")

    if policy.canonical_ignore_root and canonical.path == "/" and page.path != "/":
        return CanonicalResult(None, "root_canonical")

    page_info = pagination_info(page)
    if page_info is not None and page_info.number > 1:
        canon_info = pagination_info(canonical)
        if canon_info is None or canon_info.number != page_info.number:
            return CanonicalResult(None, "drops_pagination")

    return CanonicalResult(canonical.url, "", canonical.url != page.url)
