"""Website metadata enrichment (Phase 12.1). Pure: no network, clock, randomness or database.

`WebsiteMetadata` is the descriptive metadata of a site's pages: title, meta description/keywords, favicon,
canonical URL, Open Graph and Twitter card values. It maps values the existing HTML parser already extracted
(`ParsedPage.title` and `ParsedPage.metadata`); nothing is parsed or fetched here.

Rules:

- Every field is optional. Missing or blank means `None`; an all-empty record is "no metadata" (`is_empty`).
- Text is whitespace-collapsed; characters are otherwise untouched (Persian text and ZWNJ survive).
  `twitter_card` is lower-cased (it is a token such as `summary_large_image`).
- URL fields (`favicon_url`, `canonical_url`, `og_image`, `og_url`, `twitter_image`) are resolved against the page
  URL and normalised with the URL engine (`normalize_url`, any port, like `BusinessLead`). A value that is not a
  usable http(s) URL (`data:`, `javascript:`, garbage) becomes `None`; it never raises. No favicon is guessed
  when the page declares none.
- `merge_metadata(existing, incoming)` folds another page of the same website into the stored record. The stored
  value wins per field; the incoming page only fills what is missing. So pages of one site never duplicate or
  overwrite metadata, merging the same data again changes nothing, and the result is deterministic.
- `to_dict()` has fixed keys (JSON column); `from_dict(to_dict())` round-trips.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from app.leads.errors import LeadDataError
from app.urls.errors import UrlRejected
from app.urls.normalize import normalize_url
from app.urls.policy import UrlPolicy

if TYPE_CHECKING:  # pragma: no cover
    from app.crawler.html_parser import ParsedPage

_POLICY = UrlPolicy(allowed_ports=None)  # same policy as BusinessLead URLs: any port, data not a crawl target
_URL_FIELDS = ("favicon_url", "canonical_url", "og_image", "og_url", "twitter_image")


def _text(value: object, *, lower: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text.lower() if lower else text


def _url(value: object, base: str | None) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return normalize_url(urljoin(base, text) if base else text, policy=_POLICY).url
    except (UrlRejected, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class WebsiteMetadata:
    title: str | None = None
    description: str | None = None
    keywords: str | None = None
    favicon_url: str | None = None
    canonical_url: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_image: str | None = None
    og_url: str | None = None
    twitter_card: str | None = None
    twitter_title: str | None = None
    twitter_description: str | None = None
    twitter_image: str | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None and not isinstance(value, str):
                raise LeadDataError(f"{field.name} must be a string or None, got {type(value).__name__}")
            if field.name in _URL_FIELDS:
                object.__setattr__(self, field.name, _url(value, None))
            else:
                object.__setattr__(self, field.name, _text(value, lower=field.name == "twitter_card"))

    @property
    def is_empty(self) -> bool:
        return all(getattr(self, field.name) is None for field in fields(self))

    def to_dict(self) -> dict[str, str | None]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WebsiteMetadata:
        """Rebuild from `to_dict()` output. Missing keys are fine; unknown keys and wrong types raise."""
        if not isinstance(data, Mapping):
            raise LeadDataError(f"website metadata must be a mapping, got {type(data).__name__}")
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(data) - known, key=str)
        if unknown:
            raise LeadDataError(f"unknown website metadata fields: {', '.join(map(str, unknown))}")
        return cls(**{key: data[key] for key in data})

    @classmethod
    def from_parsed_page(cls, parsed: ParsedPage, *, source_url: str | None = None) -> WebsiteMetadata:
        """Map the already extracted values of one page. Relative URLs are resolved against `source_url`."""
        meta = parsed.metadata
        resolved = {name: _url(getattr(meta, name), source_url) for name in _URL_FIELDS}
        return cls(
            title=_text(parsed.title),
            description=_text(meta.description),
            keywords=_text(meta.keywords),
            og_title=_text(meta.og_title),
            og_description=_text(meta.og_description),
            twitter_card=_text(meta.twitter_card, lower=True),
            twitter_title=_text(meta.twitter_title),
            twitter_description=_text(meta.twitter_description),
            **resolved,
        )


def merge_metadata(existing: WebsiteMetadata | None, incoming: WebsiteMetadata | None) -> WebsiteMetadata | None:
    """`existing` plus whatever `incoming` adds: per field the existing value wins, a missing one is filled.

    Returns None when neither has any metadata. Idempotent and never overwrites a stored value.
    """
    if existing is None or existing.is_empty:
        return incoming if incoming is not None and not incoming.is_empty else None
    if incoming is None or incoming.is_empty:
        return existing
    return WebsiteMetadata(**{
        field.name: getattr(existing, field.name) or getattr(incoming, field.name) for field in fields(WebsiteMetadata)
    })
