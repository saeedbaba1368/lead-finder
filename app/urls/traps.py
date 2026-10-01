"""Crawl-trap detection: pagination, calendars, repeated paths and query explosions.

`detect_stateless_trap` inspects one URL in isolation. `TrapTracker` adds per-crawl state
(caps on query variants, calendar patterns, and pages beyond a known-empty page).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote

from app.urls.errors import RejectReason
from app.urls.normalize import NormalizedUrl
from app.urls.policy import UrlPolicy

Trap = tuple[RejectReason, str]

# `p` and `start`/`from` are deliberately absent: WordPress uses `?p=<post id>` and
# `start`/`from` are frequently dates or ids.
PAGE_PARAMS: frozenset[str] = frozenset(
    {"page", "paged", "pg", "pagenum", "page_num", "pageno", "page_no", "pagenumber",
     "currentpage", "current_page", "pn", "pagenr"}
)
OFFSET_PARAMS: frozenset[str] = frozenset({"offset", "skip", "startindex", "start_index"})
_DATE_VALUE_PARAMS = frozenset(
    {"date", "start_date", "end_date", "startdate", "enddate", "eventdate", "event_date",
     "tribe-bar-date", "cal_date", "calendar_date", "ym", "yearmonth", "year_month", "dt", "day"}
)
_YEAR_PARAMS = frozenset({"year", "yr"})
_CALENDAR_FLAG_PARAMS = frozenset({"month", "mo", "week", "cal", "calendar", "cal_month", "calmonth"})
_CALENDAR_WORDS = frozenset(
    {"archive", "archives", "calendar", "events", "event", "schedule", "timeline", "year", "cal"}
)

# 2026, 2026-05-01, 2026/05, and compact 202605 / 20260501; but not 20265 or 2026123.
_YEAR_IN_VALUE = re.compile(r"(?<!\d)((?:19|20|21)\d{2})(?=(?:\d{2}){1,2}(?!\d)|(?!\d))")
_PATH_DATE = re.compile(r"/((?:19|20|21)\d{2})[/-](0?[1-9]|1[0-2])(?:[/-](0?[1-9]|[12]\d|3[01]))?$")
_PATH_YEAR_ONLY = re.compile(r"^(?:19|20|21)\d{2}$")
_PAGE_SEGMENT = re.compile(r"^page[-_]?(\d{1,9})$", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_MAX_INT_DIGITS = 9


@dataclass(frozen=True, slots=True)
class PageInfo:
    number: int
    base_key: str  # identifies the listing regardless of which page it is


def _to_int(text: str) -> int | None:
    text = text.strip()
    if not text.isascii() or not text.isdigit():
        return None
    return int(text) if len(text) <= _MAX_INT_DIGITS else 10**_MAX_INT_DIGITS


def _path_segments(n: NormalizedUrl) -> list[str]:
    return [unquote(s) for s in n.segments]


def pagination_info(n: NormalizedUrl) -> PageInfo | None:
    """Detect `?page=N`, `/page/N`, `/page-N` style pagination."""
    segments = _path_segments(n)
    number: int | None = None
    base_segments = segments

    for i, seg in enumerate(segments):
        m = _PAGE_SEGMENT.match(seg)
        if m:
            number = int(m.group(1)) if len(m.group(1)) <= _MAX_INT_DIGITS else 10**_MAX_INT_DIGITS
            base_segments = segments[:i] + segments[i + 1 :]
            break
        if seg.lower() == "page" and i + 1 < len(segments):
            value = _to_int(segments[i + 1])
            if value is not None:
                number = value
                base_segments = segments[:i] + segments[i + 2 :]
                break

    rest: list[str] = []
    for name, value in n.params:
        if name.lower() in PAGE_PARAMS:
            parsed = _to_int(value)
            if parsed is not None and number is None:
                number = parsed
            continue
        rest.append(f"{name}={value}")
    if number is None:
        return None
    base = f"{n.host}/{'/'.join(base_segments)}?{'&'.join(sorted(rest))}"
    return PageInfo(number=number, base_key=base)


def _offset_value(n: NormalizedUrl) -> int | None:
    for name, value in n.params:
        if name.lower() in OFFSET_PARAMS:
            parsed = _to_int(value)
            if parsed is not None:
                return parsed
    return None


@dataclass(frozen=True, slots=True)
class CalendarInfo:
    years: tuple[int, ...]  # years referenced by the URL (may be empty)


def calendar_info(n: NormalizedUrl) -> CalendarInfo | None:
    """Return calendar details if the URL looks like a calendar/archive *listing*."""
    segments = _path_segments(n)
    years: list[int] = []
    found = False

    match = _PATH_DATE.search("/" + "/".join(segments)) if segments else None
    if match:
        found = True
        years.append(int(match.group(1)))
    elif len(segments) >= 2 and _PATH_YEAR_ONLY.match(segments[-1]) and (
        segments[-2].lower() in _CALENDAR_WORDS
    ):
        found = True
        years.append(int(segments[-1]))
    if any(s.lower() in {"calendar", "ical"} for s in segments):
        found = True

    for name, value in n.params:
        lname = name.lower()
        if lname in _YEAR_PARAMS:
            m = _YEAR_IN_VALUE.fullmatch(value.strip())
            if m:
                found = True
                years.append(int(m.group(1)))
        elif lname in _DATE_VALUE_PARAMS:
            m = _YEAR_IN_VALUE.search(value)
            if m:
                found = True
                years.append(int(m.group(1)))
        elif lname in _CALENDAR_FLAG_PARAMS:
            found = True
    return CalendarInfo(tuple(years)) if found else None


def has_repeating_cycle(segments: list[str], max_repeats: int) -> bool:
    """True if a run of 1-3 segments repeats back-to-back `max_repeats`+ times."""
    n = len(segments)
    for size in (1, 2, 3):
        if size * max_repeats > n:
            continue
        for start in range(0, n - size * max_repeats + 1):
            block = segments[start : start + size]
            if all(
                segments[start + k * size : start + (k + 1) * size] == block
                for k in range(1, max_repeats)
            ):
                return True
    return False


def detect_stateless_trap(n: NormalizedUrl, policy: UrlPolicy, today: date) -> Trap | None:
    """Detect traps visible from a single URL. Returns (reason, detail) or None."""
    segments = _path_segments(n)
    if len(segments) > policy.max_path_depth:
        return RejectReason.TRAP_DEPTH, f"{len(segments)} segments"
    if has_repeating_cycle(segments, policy.max_segment_repeats):
        return RejectReason.TRAP_REPEATED_SEGMENTS, "/".join(segments[-6:])
    if len(n.params) > policy.max_query_params:
        return RejectReason.TRAP_QUERY_PARAMS, f"{len(n.params)} params"

    page = pagination_info(n)
    if page is not None and page.number > policy.max_page_number:
        return RejectReason.TRAP_PAGINATION, f"page {page.number}"
    offset = _offset_value(n)
    if offset is not None and offset > policy.max_offset:
        return RejectReason.TRAP_PAGINATION, f"offset {offset}"

    cal = calendar_info(n)
    if cal is not None:
        low = today.year - policy.calendar_years_back
        high = today.year + policy.calendar_years_ahead
        for year in cal.years:
            if not low <= year <= high:
                return RejectReason.TRAP_CALENDAR, f"year {year} outside {low}-{high}"
    return None


def pattern_signature(n: NormalizedUrl) -> str:
    """Shape of a URL with every number masked, used to group calendar-style URLs."""
    path = _DIGITS.sub("#", "/".join(_path_segments(n)))
    query = sorted(f"{name.lower()}={_DIGITS.sub('#', value)}" for name, value in n.params)
    return f"{n.host}/{path}?{'&'.join(query)}"


class TrapTracker:
    """Per-crawl trap state. Feed every URL through `admit`; only admitted URLs count."""

    def __init__(self, policy: UrlPolicy) -> None:
        self._policy = policy
        self._variants: dict[str, set[str]] = {}
        self._calendar: dict[str, set[str]] = {}
        self._empty_from: dict[str, int] = {}

    def record_empty_page(self, n: NormalizedUrl) -> None:
        """Note that page N of a listing had no new content: later pages are traps."""
        page = pagination_info(n)
        if page is None:
            return
        current = self._empty_from.get(page.base_key)
        self._empty_from[page.base_key] = page.number if current is None else min(current, page.number)

    def check(self, n: NormalizedUrl) -> Trap | None:
        """Non-mutating stateful check."""
        page = pagination_info(n)
        if page is not None:
            cutoff = self._empty_from.get(page.base_key)
            if cutoff is not None and page.number >= cutoff:
                return RejectReason.TRAP_PAGINATION, f"page {page.number} follows empty page {cutoff}"

        variant_key, variant = self._variant_of(n, page)
        if variant:
            seen = self._variants.get(variant_key, set())
            if variant not in seen and len(seen) >= self._policy.max_query_variants_per_path:
                return RejectReason.TRAP_QUERY_VARIANTS, f"{len(seen)} variants of {n.path}"

        if calendar_info(n) is not None:
            signature = pattern_signature(n)
            seen_urls = self._calendar.get(signature, set())
            if n.url not in seen_urls and len(seen_urls) >= self._policy.max_calendar_urls_per_pattern:
                return RejectReason.TRAP_CALENDAR, f"{len(seen_urls)} urls for one calendar pattern"
        return None

    def admit(self, n: NormalizedUrl) -> Trap | None:
        """Check, and if the URL is fine, count it. Returns a trap or None."""
        trap = self.check(n)
        if trap is not None:
            return trap
        page = pagination_info(n)
        variant_key, variant = self._variant_of(n, page)
        if variant:
            self._variants.setdefault(variant_key, set()).add(variant)
        if calendar_info(n) is not None:
            self._calendar.setdefault(pattern_signature(n), set()).add(n.url)
        return None

    @staticmethod
    def _variant_of(n: NormalizedUrl, page: PageInfo | None) -> tuple[str, str]:
        """Query string without page params: page-only differences are not 'variants'."""
        if not n.query:
            return "", ""
        kept = [f"{k}={v}" for k, v in n.params if k.lower() not in PAGE_PARAMS]
        return f"{n.host}{n.path}", "&".join(kept)


Clock = Callable[[], date]
