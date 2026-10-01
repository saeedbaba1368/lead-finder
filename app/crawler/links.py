"""Link extraction from HTML (Phase 6.2). Pure parsing, no I/O, no URL policy.

Returns raw `href` values (stripped, de-duplicated by exact text, in document order) plus the
document base URL. Resolving, normalising and scoping are done by the URL engine (`UrlGate`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_LINKS_PER_PAGE = 10_000  # hard safety cap on hrefs collected from one document
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_LINK_TAGS = frozenset({"a", "area"})


@dataclass(frozen=True, slots=True)
class LinkExtraction:
    base_url: str  # page URL, or the resolved `<base href>` when present and valid
    hrefs: tuple[str, ...] = ()  # unique candidate hrefs, document order
    skipped_non_http: int = 0  # mailto:, tel:, javascript:, data:, ftp: ...
    skipped_fragment_only: int = 0  # "#section"
    skipped_empty: int = 0
    truncated: bool = False  # MAX_LINKS_PER_PAGE reached


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.raw: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base":
            if self.base_href is None:
                href = dict(attrs).get("href")
                if href and href.strip():
                    self.base_href = href.strip()
        elif tag in _LINK_TAGS:
            href = dict(attrs).get("href")
            if href is not None:
                self.raw.append(href)


def extract_links(html: str, page_url: str) -> LinkExtraction:
    """Extract candidate link hrefs from `html`. Never raises; malformed markup is tolerated."""
    collector = _Collector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:  # noqa: BLE001 - html.parser can raise on pathological input; keep what we have
        pass
    base = page_url
    if collector.base_href:
        try:
            joined = urljoin(page_url, collector.base_href)
            parts = urlsplit(joined)
            if parts.scheme in ("http", "https") and parts.hostname:
                base = joined
        except ValueError:  # malformed <base href>: ignore it
            pass

    seen: set[str] = set()
    hrefs: list[str] = []
    non_http = fragment = empty = 0
    truncated = False
    for raw in collector.raw:
        href = raw.strip()
        if not href:
            empty += 1
        elif href.startswith("#"):
            fragment += 1
        elif (m := _SCHEME.match(href)) and m.group(0).lower() not in ("http:", "https:"):
            non_http += 1
        elif href not in seen:
            if len(hrefs) >= MAX_LINKS_PER_PAGE:
                truncated = True
                break
            seen.add(href)
            hrefs.append(href)
    return LinkExtraction(base, tuple(hrefs), non_http, fragment, empty, truncated)
