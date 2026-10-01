"""Content-type eligibility: which resources are worth fetching and parsing as pages."""

from __future__ import annotations

from app.urls.errors import RejectReason
from app.urls.policy import UrlPolicy

# Extensions that never yield an HTML page worth parsing for contact/people data.
NON_HTML_EXTENSIONS: frozenset[str] = frozenset(
    {
        # images / vector
        "jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "ico", "svg", "tif", "tiff", "heic",
        # audio / video
        "mp3", "mp4", "m4a", "m4v", "wav", "ogg", "webm", "avi", "mov", "wmv", "flv", "mkv", "mpg", "mpeg",
        # archives / binaries
        "zip", "gz", "tgz", "tar", "rar", "7z", "bz2", "xz", "exe", "msi", "dmg", "pkg", "apk",
        "deb", "rpm", "iso", "bin", "jar",
        # web assets / data / feeds
        "css", "js", "mjs", "map", "json", "xml", "rss", "atom", "wasm", "csv",
        # fonts
        "woff", "woff2", "ttf", "otf", "eot",
        # office documents / calendars / contacts
        "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "ics", "vcf",
    }
)
_PDF_EXTENSION = "pdf"
_PLAIN_TEXT_EXTENSION = "txt"

HTML_MEDIA_TYPES: frozenset[str] = frozenset({"text/html", "application/xhtml+xml"})
_PLAIN_TEXT_MEDIA_TYPES: frozenset[str] = frozenset({"text/plain"})
_PDF_MEDIA_TYPES: frozenset[str] = frozenset({"application/pdf"})


def path_extension(path: str) -> str:
    """Lower-case extension of the last path segment ('' if none or implausibly long)."""
    last = path.rsplit("/", 1)[-1].split(";", 1)[0]
    if "." not in last:
        return ""
    ext = last.rsplit(".", 1)[-1].lower()
    return ext if 0 < len(ext) <= 8 else ""


def check_extension(path: str, policy: UrlPolicy) -> str | None:
    """Return the offending extension if the path is not an eligible resource, else None."""
    ext = path_extension(path)
    if not ext:
        return None
    if ext == _PDF_EXTENSION:
        return None if policy.allow_pdf else ext
    if ext == _PLAIN_TEXT_EXTENSION:
        return None if policy.allow_plain_text else ext
    return ext if ext in NON_HTML_EXTENSIONS else None


def parse_media_type(header: str | None) -> str | None:
    """`'Text/HTML; charset=UTF-8'` -> `'text/html'`; None for missing/blank headers."""
    if header is None:
        return None
    media = header.split(";", 1)[0].strip().lower()
    return media or None


def eligible_media_types(policy: UrlPolicy) -> frozenset[str]:
    types = set(HTML_MEDIA_TYPES)
    if policy.allow_plain_text:
        types |= _PLAIN_TEXT_MEDIA_TYPES
    if policy.allow_pdf:
        types |= _PDF_MEDIA_TYPES
    return frozenset(types)


def check_content_type(header: str | None, policy: UrlPolicy) -> RejectReason | None:
    """Check a `Content-Type` response header. None means eligible."""
    media = parse_media_type(header)
    if media is None:
        return None if policy.allow_missing_content_type else RejectReason.CONTENT_TYPE_NOT_ELIGIBLE
    return None if media in eligible_media_types(policy) else RejectReason.CONTENT_TYPE_NOT_ELIGIBLE


def check_content_length(value: str | int | None, policy: UrlPolicy) -> RejectReason | None:
    """Check a `Content-Length` header. Missing/garbage values pass (the fetcher enforces a
    streaming cap); only a declared size above the policy limit is rejected."""
    if value is None:
        return None
    try:
        length = int(str(value).strip())
    except ValueError:
        return None
    return RejectReason.CONTENT_TOO_LARGE if length > policy.max_content_length else None
