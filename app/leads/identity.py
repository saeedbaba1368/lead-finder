"""Business identity extraction (Phase 8.2): name, website, domain and description of the business behind a page.

Pure and deterministic: it reads an already parsed page (`ParsedPage`), never fetches anything and never changes
crawling. `extract_identity(parsed, source_url=..., website=None)` returns a `BusinessIdentity`.

Business name, first usable signal wins (each source offers at most one candidate):

1. `json_ld`: the `name` of a schema.org business record (Person records are not businesses). A record whose
   `url` belongs to another registrable domain is not about this site and is skipped; one that matches the site
   is preferred.
2. `og_site_name`, then `og_title` (Open Graph). The site name is used as written; the Open Graph title is
   reduced to its brand part like a page title.
3. `title`: the `<title>`, reduced to its brand part.
4. `homepage_heading`: only on a homepage, the first `<h1>`, and only if it is short and not a sentence.

Brand part of a title (`Contact us | Acme Plumbing`): the title is split at separators (`|  -  –  —  :  ·  •  »`);
generic segments (`Home`, `Contact us`, `خانه`, `تماس با ما`, ...) are dropped; a segment that matches the domain
(`acme-plumbing.example` ~ "Acme Plumbing") wins; otherwise the first segment on a homepage and the last one on
other pages (the usual "Page | Site" convention). A single-segment title of an inner page names the page, not the
business, so it is only accepted when it matches the domain.

`candidates` lists every source's candidate in priority order; `business_name` is the first one. `conflict` is True
when two candidates name different things (after case, punctuation, spacing, ZWNJ and Arabic/Persian letter
normalisation, one containing the other still counts as the same name); the priority order still decides.
No name is invented: if no signal qualifies, `business_name` is None.

`website` defaults to the origin of `source_url`; `domain` is its registrable domain (IDN hosts in punycode).
`description`: JSON-LD description, then the meta description, then `og:description`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.crawler.classify import PageCategory
from app.leads.errors import LeadDataError
from app.urls.domain_identity import WEBSITE_POLICY, domain_identity
from app.urls.errors import UrlRejected
from app.urls.normalize import normalize_url

if TYPE_CHECKING:  # pragma: no cover
    from app.crawler.html_parser import ParsedPage

SOURCE_JSON_LD = "json_ld"
SOURCE_OG_SITE_NAME = "og_site_name"
SOURCE_OG_TITLE = "og_title"
SOURCE_TITLE = "title"
SOURCE_HOMEPAGE_HEADING = "homepage_heading"
SOURCES = (SOURCE_JSON_LD, SOURCE_OG_SITE_NAME, SOURCE_OG_TITLE, SOURCE_TITLE, SOURCE_HOMEPAGE_HEADING)  # priority

DESCRIPTION_JSON_LD = "json_ld"
DESCRIPTION_META = "meta_description"
DESCRIPTION_OG = "og_description"

MAX_NAME_LENGTH = 120  # longer text is a sentence, not a name
MAX_HEADING_WORDS = 5
MAX_HEADING_LENGTH = 60
MIN_DOMAIN_MATCH = 3  # letters needed before a domain label is compared with a name

_SEPARATORS = re.compile(r"\s*[|•·»«–—]\s*|\s+-\s+|\s*:\s+|\s+/\s+")
_SENTENCE_END = re.compile(r"[.!?؟:;،,…]$")
_LETTER_FORMS = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"})  # Arabic ي ى ك -> Persian ی ک
_URL_OR_EMAIL = re.compile(r"https?://|www\.|@")

_GENERIC = frozenset(  # compared after `_fold`
    {
        "home", "homepage", "home page", "welcome", "main", "main page", "index", "official website", "official site",
        "website", "contact", "contact us", "contacts", "get in touch", "about", "about us", "services", "our services",
        "products", "our products", "blog", "news", "portfolio", "our work", "careers", "jobs", "login", "sign in",
        "خانه", "صفحه اصلی", "صفحه نخست", "خوش آمدید", "سایت رسمی", "وب سایت رسمی", "وبسایت رسمی", "تماس", "تماس با ما",
        "درباره", "درباره ما", "خدمات", "خدمات ما", "محصولات", "وبلاگ", "اخبار", "نمونه کار", "نمونه کارها", "استخدام",
    }
)


# ----------------------------------------------------------------------------- result
@dataclass(frozen=True, slots=True)
class NameCandidate:
    name: str
    source: str  # one of SOURCES

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "source": self.source}


@dataclass(frozen=True, slots=True)
class BusinessIdentity:
    website: str  # normalised http(s) URL
    domain: str  # registrable domain of the website host
    business_name: str | None = None
    name_source: str | None = None  # the source of `business_name` (one of SOURCES)
    candidates: tuple[NameCandidate, ...] = ()  # every source's candidate, priority order; the first is the name
    conflict: bool = False  # candidates name different things
    description: str | None = None
    description_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_name": self.business_name,
            "name_source": self.name_source,
            "website": self.website,
            "domain": self.domain,
            "description": self.description,
            "description_source": self.description_source,
            "candidates": [c.to_dict() for c in self.candidates],
            "conflict": self.conflict,
        }


# ----------------------------------------------------------------------------- website / domain
def normalize_website(url: object, name: str = "website") -> str:
    """Normalise an http(s) URL with the URL engine (any port). Raises `LeadDataError`."""
    if not isinstance(url, str) or not url.strip():
        raise LeadDataError(f"{name} is required")
    try:
        return normalize_url(url.strip(), policy=WEBSITE_POLICY).url
    except UrlRejected as exc:
        raise LeadDataError(f"{name} is not a usable http(s) URL: {exc}") from exc


def domain_of(website: str) -> str:
    """Canonical domain identity of a website URL (`app.urls.domain_identity`): the registrable domain, never with
    `www.`; the host itself for IPs, single labels and public suffixes. Raises `LeadDataError` for an unusable URL."""
    try:
        return domain_identity(website, default_scheme=None).domain
    except UrlRejected as exc:
        raise LeadDataError(f"website is not a usable http(s) URL: {exc}") from exc


def same_site(url: object, domain: str) -> bool:
    """True if `url` (any spelling, scheme optional) belongs to the canonical `domain`; an unusable URL does not."""
    try:
        return domain_identity(url).domain == domain
    except UrlRejected:
        return False


def website_origin(url: object) -> str:
    """`scheme://host[:port]` of a URL, normalised. Raises `LeadDataError`."""
    if not isinstance(url, str):
        raise LeadDataError(f"source_url must be a string, got {type(url).__name__}")
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        raise LeadDataError(f"cannot derive a website from source_url {url!r}")
    return normalize_website(f"{parts.scheme}://{parts.netloc}")


# ----------------------------------------------------------------------------- name helpers
def _fold(text: str) -> str:
    """Comparison form: NFKC, case-folded, Arabic letter forms unified, spaces collapsed (ZWNJ counts as a space)."""
    text = unicodedata.normalize("NFKC", text).translate(_LETTER_FORMS).casefold().replace("\u200c", " ")
    return " ".join(text.split())


def _squash(text: str) -> str:
    """Letters and digits only (any script): `Acme-Plumbing!` and `acme plumbing` compare equal."""
    return "".join(ch for ch in _fold(text) if ch.isalnum())


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > MAX_NAME_LENGTH or _URL_OR_EMAIL.search(name) or not _squash(name):
        return None
    return None if _fold(name) in _GENERIC else name


def _domain_label(domain: str) -> str:
    host = domain
    try:
        host = domain.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        pass
    return _squash(host.split(".")[0])


def _matches_domain(segment: str, label: str) -> bool:
    key = _squash(segment)
    return len(label) >= MIN_DOMAIN_MATCH and len(key) >= MIN_DOMAIN_MATCH and (label in key or key in label)


def _brand_from_title(title: str | None, *, label: str, homepage: bool) -> str | None:
    if not title:
        return None
    segments = [" ".join(part.split()) for part in _SEPARATORS.split(title)]
    segments = [s for s in segments if s]
    usable = [name for s in segments if (name := _clean_name(s)) is not None]
    if not usable:
        return None
    for name in usable:
        if _matches_domain(name, label):
            return name
    if len(segments) == 1:  # one segment: the site name on a homepage, otherwise the name of the page
        return usable[0] if homepage else None
    return usable[0] if homepage else usable[-1]


def _from_json_ld(parsed: ParsedPage, domain: str) -> str | None:
    matching: list[str] = []
    neutral: list[str] = []
    for record in parsed.structured_data:
        if record.type == "Person" or (name := _clean_name(record.name)) is None:
            continue
        if record.url:
            if not same_site(record.url, domain):
                continue  # about another site
            matching.append(name)
        else:
            neutral.append(name)
    return next(iter(matching or neutral), None)


def _from_homepage_heading(parsed: ParsedPage, *, homepage: bool) -> str | None:
    if not homepage:
        return None
    heading = next((h.text for h in parsed.headings if h.level == 1), None)
    name = _clean_name(heading)
    if name is None or len(name) > MAX_HEADING_LENGTH or len(name.split()) > MAX_HEADING_WORDS or _SENTENCE_END.search(name):
        return None
    return name


def _same_name(a: str, b: str) -> bool:
    x, y = _squash(a), _squash(b)
    return x == y or x in y or y in x


# ----------------------------------------------------------------------------- public API
def extract_identity(parsed: ParsedPage, *, source_url: str, website: str | None = None) -> BusinessIdentity:
    """Business identity of the site that `parsed` (fetched from `source_url`) belongs to.

    Raises `LeadDataError` only for an unusable `source_url` / `website`; missing page data just gives None values."""
    site = normalize_website(website, "website") if website is not None else website_origin(source_url)
    domain = domain_of(site)
    label = _domain_label(domain)
    homepage = parsed.classification.category is PageCategory.HOMEPAGE
    meta = parsed.metadata

    found = (
        (SOURCE_JSON_LD, _from_json_ld(parsed, domain)),
        (SOURCE_OG_SITE_NAME, _clean_name(meta.og_site_name)),
        (SOURCE_OG_TITLE, _brand_from_title(meta.og_title, label=label, homepage=homepage)),
        (SOURCE_TITLE, _brand_from_title(parsed.title, label=label, homepage=homepage)),
        (SOURCE_HOMEPAGE_HEADING, _from_homepage_heading(parsed, homepage=homepage)),
    )
    candidates = tuple(NameCandidate(name, source) for source, name in found if name is not None)
    conflict = any(not _same_name(a.name, b.name) for i, a in enumerate(candidates) for b in candidates[i + 1:])

    description, description_source = None, None
    record_description = next((r.description for r in parsed.structured_data if r.type != "Person" and r.description), None)
    for text, source in (
        (record_description, DESCRIPTION_JSON_LD), (meta.description, DESCRIPTION_META), (meta.og_description, DESCRIPTION_OG),
    ):
        if isinstance(text, str) and text.strip():
            description, description_source = text.strip(), source
            break

    first = candidates[0] if candidates else None
    return BusinessIdentity(
        website=site, domain=domain,
        business_name=first.name if first else None, name_source=first.source if first else None,
        candidates=candidates, conflict=conflict,
        description=description, description_source=description_source,
    )
