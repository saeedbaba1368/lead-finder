"""robots.txt parsing. Phase 5.1 extracts `Sitemap:` declarations and keeps the other
directives as data; it does not evaluate Allow/Disallow rules against URLs."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

MAX_SITEMAP_DECLARATIONS = 1000


@dataclass(slots=True)
class RobotsGroup:
    user_agents: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    disallow: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RobotsResult:
    sitemaps: list[str] = field(default_factory=list)  # valid, de-duplicated, in order
    groups: list[RobotsGroup] = field(default_factory=list)
    invalid_sitemaps: int = 0
    empty: bool = False


def decode_text(data: bytes) -> str:
    """Decode bytes leniently (BOM-aware); undecodable bytes never raise."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def parse_robots(content: bytes | str) -> RobotsResult:
    """Parse robots.txt text. Malformed lines are skipped; this never raises."""
    text = decode_text(content) if isinstance(content, bytes) else content
    result = RobotsResult(empty=not text.strip())
    group: RobotsGroup | None = None
    last_was_agent = False
    seen: set[str] = set()
    for raw_line in text.replace("\x00", "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name == "sitemap":  # independent of user-agent groups
            if not value or not _looks_like_http_url(value):
                result.invalid_sitemaps += 1
            elif value not in seen and len(result.sitemaps) < MAX_SITEMAP_DECLARATIONS:
                seen.add(value)
                result.sitemaps.append(value)
        elif name == "user-agent":
            if group is None or not last_was_agent:
                group = RobotsGroup()
                result.groups.append(group)
            if value:
                group.user_agents.append(value)
            last_was_agent = True
            continue
        elif name in ("allow", "disallow") and group is not None:
            (group.allow if name == "allow" else group.disallow).append(value)
        last_was_agent = False
    return result


def _looks_like_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)
