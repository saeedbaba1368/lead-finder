"""Social media profile detection (Phase 7.2.3). Pure: no network, no crawling, no URL-engine involvement.

`classify_social_url(href)` decides whether one raw `<a href>` value is a *profile* URL of a supported platform
and returns it in canonical form (`SocialLink`): `https`, canonical host, no query string or fragment, no
trailing slash, the path as written (case kept). Share buttons, posts, videos, login pages and similar are not
profiles and return None. Only absolute `http(s)://` and protocol-relative `//` hrefs are considered; a
scheme-less `facebook.com/acme` is a relative path in HTML, so it is not treated as a social link.

Platforms are data: a `SocialPlatform` (name, domains, canonical host, path matcher). To add a platform, write
one matcher function and append one `SocialPlatform(...)` to `SOCIAL_PLATFORMS`; nothing else changes.
Detecting a link never causes it to be fetched: the crawler's scope policy (unchanged) keeps external domains
out of the queue.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True, slots=True)
class SocialLink:
    platform: str  # "facebook" | "instagram" | "linkedin" | "x" | "youtube" | "tiktok" (or an added platform)
    url: str  # canonical profile URL


# A matcher gets the non-empty path segments and the parsed query string; it returns the canonical path
# (starting with "/", may carry a required query such as "?id=123") or None when the URL is not a profile.
PathMatcher = Callable[[list[str], dict[str, list[str]]], "str | None"]

_WEB_SUBDOMAINS = frozenset({"www", "m", "mobile", "web", "mbasic", "touch"})
MAX_SOCIAL_LINKS_PER_PAGE = 200


@dataclass(frozen=True, slots=True)
class SocialPlatform:
    name: str
    canonical_host: str
    domains: frozenset[str]  # registrable domains, aliases included (e.g. fb.me)
    match: PathMatcher
    subdomains: frozenset[str] = _WEB_SUBDOMAINS  # only these prefixes count as the same site
    country_subdomains: bool = False  # also accept two-letter prefixes (uk.linkedin.com)


# ----------------------------------------------------------------------------- matchers
_DIGITS = re.compile(r"[0-9]+")


def _plain(segments: list[str], pattern: re.Pattern[str], reserved: frozenset[str]) -> str | None:
    """`/<handle>` and nothing else."""
    if len(segments) != 1:
        return None
    handle = segments[0]
    if handle.lower() in reserved or not pattern.fullmatch(handle):
        return None
    return "/" + handle


_FB_NAME = re.compile(r"[A-Za-z0-9._-]{2,100}")
_FB_RESERVED = frozenset(
    "about ads bookmarks business careers dialog events flx friends fundraisers gaming groups hashtag help home.php "
    "l.php legal login login.php logout marketplace messages messenger notifications pages people permalink.php "
    "photo photos plugins policies privacy profile.php public recover reel reels search settings share share.php "
    "sharer sharer.php stories story.php tr video videos watch whatsapp".split()
)


def _facebook(segments: list[str], query: dict[str, list[str]]) -> str | None:
    if not segments:
        return None
    first = segments[0].lower()
    if first == "profile.php":
        profile_id = query.get("id", [""])[0]
        return f"/profile.php?id={profile_id}" if len(segments) == 1 and _DIGITS.fullmatch(profile_id) else None
    if first in ("pages", "people"):  # /pages/<name>/<numeric id>
        ok = len(segments) == 3 and _DIGITS.fullmatch(segments[2]) is not None
        return "/" + "/".join(segments) if ok else None
    if first == "groups":
        return "/groups/" + segments[1] if len(segments) == 2 and _FB_NAME.fullmatch(segments[1]) else None
    if first.endswith(".php"):
        return None
    return _plain(segments, _FB_NAME, _FB_RESERVED)


_IG_NAME = re.compile(r"[A-Za-z0-9._]{1,30}")
_IG_RESERVED = frozenset(
    "about accounts api challenge developer direct directory emails explore legal locations nametag oauth p press "
    "privacy reel reels s share stories tags tv video web".split()
)


def _instagram(segments: list[str], query: dict[str, list[str]]) -> str | None:
    return _plain(segments, _IG_NAME, _IG_RESERVED)


_LINKEDIN_KINDS = frozenset({"in", "company", "school", "showcase"})
_LINKEDIN_SLUG = re.compile(r"[^/\s]{1,200}")  # percent-encoded or Unicode slugs are legal


def _linkedin(segments: list[str], query: dict[str, list[str]]) -> str | None:
    if len(segments) != 2 or segments[0].lower() not in _LINKEDIN_KINDS:
        return None
    return f"/{segments[0].lower()}/{segments[1]}" if _LINKEDIN_SLUG.fullmatch(segments[1]) else None


_X_NAME = re.compile(r"[A-Za-z0-9_]{1,15}")
_X_RESERVED = frozenset(
    "about account compose download explore hashtag home i intent login messages notifications privacy search "
    "settings share signup tos".split()
)


def _x(segments: list[str], query: dict[str, list[str]]) -> str | None:
    return _plain(segments, _X_NAME, _X_RESERVED)


_YT_HANDLE = re.compile(r"@[\w.\-%]{3,100}")
_YT_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_YT_NAME = re.compile(r"[\w.\-%]{1,100}")


def _youtube(segments: list[str], query: dict[str, list[str]]) -> str | None:
    if len(segments) == 1:
        return "/" + segments[0] if _YT_HANDLE.fullmatch(segments[0]) else None
    if len(segments) == 2:
        kind = segments[0].lower()
        if kind == "channel" and _YT_CHANNEL_ID.fullmatch(segments[1]):
            return "/channel/" + segments[1]
        if kind in ("c", "user") and _YT_NAME.fullmatch(segments[1]):
            return f"/{kind}/{segments[1]}"
    return None


_TIKTOK_HANDLE = re.compile(r"@[\w.]{1,24}")


def _tiktok(segments: list[str], query: dict[str, list[str]]) -> str | None:
    if len(segments) == 1 and _TIKTOK_HANDLE.fullmatch(segments[0]):
        return "/" + segments[0]
    return None


# ----------------------------------------------------------------------------- registry
SOCIAL_PLATFORMS: tuple[SocialPlatform, ...] = (
    SocialPlatform("facebook", "www.facebook.com", frozenset({"facebook.com", "fb.com", "fb.me"}), _facebook),
    SocialPlatform("instagram", "www.instagram.com", frozenset({"instagram.com", "instagr.am"}), _instagram),
    SocialPlatform("linkedin", "www.linkedin.com", frozenset({"linkedin.com"}), _linkedin, country_subdomains=True),
    SocialPlatform("x", "x.com", frozenset({"x.com", "twitter.com"}), _x),  # twitter.com is the same network
    SocialPlatform("youtube", "www.youtube.com", frozenset({"youtube.com"}), _youtube),  # youtu.be is video-only
    SocialPlatform("tiktok", "www.tiktok.com", frozenset({"tiktok.com"}), _tiktok),  # vm.tiktok.com is video-only
)


# ----------------------------------------------------------------------------- public API
def _host_matches(host: str, platform: SocialPlatform) -> bool:
    for domain in platform.domains:
        if host == domain:
            return True
        if host.endswith("." + domain):
            prefix = host[: -len(domain) - 1]
            if prefix in platform.subdomains or (platform.country_subdomains and re.fullmatch(r"[a-z]{2}", prefix)):
                return True
    return False


def classify_social_url(href: str, platforms: Sequence[SocialPlatform] = SOCIAL_PLATFORMS) -> SocialLink | None:
    """The canonical `SocialLink` if `href` is a profile URL of a known platform; otherwise None. Never raises."""
    try:
        value = href.strip()
        if value.startswith("//"):
            value = "https:" + value
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".")
        port = parts.port
    except (ValueError, AttributeError):
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or port not in (None, 80, 443):
        return None
    path, query = parts.path, parts.query
    if parts.fragment.startswith("!/") and path in ("", "/"):  # legacy twitter.com/#!/handle
        path, _, query = parts.fragment[1:].partition("?")
    segments = [segment for segment in path.split("/") if segment]
    for platform in platforms:
        if _host_matches(host, platform):
            canonical = platform.match(segments, parse_qs(query))
            return SocialLink(platform.name, f"https://{platform.canonical_host}{canonical}") if canonical else None
    return None


def dedupe_social_links(links: Iterable[SocialLink]) -> tuple[SocialLink, ...]:
    """Unique (case-insensitive URL), sorted by platform then URL; the first one seen is kept."""
    unique: dict[str, SocialLink] = {}
    for link in links:
        unique.setdefault(link.url.lower(), link)
    ordered = sorted(unique.values(), key=lambda link: (link.platform, link.url.lower()))
    return tuple(ordered[:MAX_SOCIAL_LINKS_PER_PAGE])


def extract_social_links(hrefs: Iterable[str], platforms: Sequence[SocialPlatform] = SOCIAL_PLATFORMS) -> tuple[SocialLink, ...]:
    """Profiles among raw hrefs: classified, de-duplicated and sorted."""
    found = (classify_social_url(href, platforms) for href in hrefs)
    return dedupe_social_links(link for link in found if link is not None)
