"""Business lead data model (Phase 8.1): one business as discovered by the crawler.

`BusinessLead` is a frozen, validated, JSON-serialisable value object. It *reuses* the existing value types
instead of redefining them: `PhoneNumber` (7.2.2), `SocialLink` (7.2.3), `Address` (7.2.4) and `PageCategory`
(7.3.2). It only holds what the crawler found; it does not copy `ParsedPage` (no text, headings, links,
metrics or raw structured data). `BusinessLead.from_parsed_page` maps the already extracted values of one
page; it extracts nothing new and guesses nothing.

Rules (all deterministic, no clock, no network):

- `website` is required: an http(s) URL, normalised with the URL engine (`normalize_url`; the port policy is
  not applied because a lead is data, not a crawl target). `domain` is the registrable domain of the website
  host (IDN hosts in punycode, like everywhere in the URL engine); it is derived, and an explicit value must
  agree with the website. `source_url` (the page the data came from) is normalised the same way.
- Text fields are stripped; blank means `None`. `business_name` also has its whitespace collapsed.
  Characters are otherwise untouched, so Persian text and ZWNJ survive.
- `emails`: lower-case, unique, sorted; `phones`: unique by `number`, sorted; `social_profiles`: unique
  (case-insensitive URL), sorted by platform then URL. A bad value raises `LeadDataError` (never silently dropped).
- `first_seen` / `last_seen`: timezone-aware, stored in UTC; `last_seen` may not be earlier than `first_seen`.
- `to_dict()` is JSON-ready with fixed keys (usable for a JSON column); `from_dict(to_dict())` round-trips.

Deliberately not here: scoring, verification, enrichment, export, CRM, cross-website de-duplication, database
tables (a later phase decides how leads are stored).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.crawler.classify import PageCategory
from app.crawler.html_parser import PhoneNumber, extract_emails_from_text
from app.crawler.social import SocialLink, dedupe_social_links
from app.crawler.structured import Address
from app.leads.errors import LeadDataError
from app.leads.identity import domain_of, extract_identity, normalize_website, same_site
from app.leads.lead_identity import LeadIdentity
from app.urls.errors import UrlRejected
from app.urls.normalize import normalize_url
from app.urls.policy import UrlPolicy

if TYPE_CHECKING:  # pragma: no cover
    from app.crawler.html_parser import ParsedPage

_LEAD_URL_POLICY = UrlPolicy(allowed_ports=None)  # any port: a lead is data, not a crawl target
_PHONE_NUMBER = re.compile(r"\+?[0-9]{3,20}")
_FIELDS = (
    "business_name", "website", "domain", "description", "emails", "phones", "address", "social_profiles",
    "page_type", "language", "source_url", "first_seen", "last_seen",
)


# ----------------------------------------------------------------------------- field normalisers
def _text(value: object, name: str, *, collapse: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LeadDataError(f"{name} must be a string or None, got {type(value).__name__}")
    value = " ".join(value.split()) if collapse else value.strip()
    return value or None


def _url(value: object, name: str) -> str | None:
    text = _text(value, name)
    if text is None:
        return None
    try:
        return normalize_url(text, policy=_LEAD_URL_POLICY).url
    except UrlRejected as exc:
        raise LeadDataError(f"{name} is not a usable http(s) URL: {exc}") from exc


def _items(value: object, name: str) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise LeadDataError(f"{name} must be a collection, got {type(value).__name__}")
    return value


def _emails(value: object) -> tuple[str, ...]:
    found: set[str] = set()
    for item in _items(value, "emails"):
        if not isinstance(item, str):
            raise LeadDataError(f"emails must contain strings, got {type(item).__name__}")
        candidate = item.strip()
        if not candidate:
            continue
        if extract_emails_from_text(candidate) != [candidate.lower()]:
            raise LeadDataError(f"invalid email address: {item!r}")
        found.add(candidate.lower())
    return tuple(sorted(found))


def _phones(value: object) -> tuple[PhoneNumber, ...]:
    unique: dict[str, PhoneNumber] = {}
    for item in _items(value, "phones"):
        if not isinstance(item, PhoneNumber):
            raise LeadDataError(f"phones must contain PhoneNumber values, got {type(item).__name__}")
        if not _PHONE_NUMBER.fullmatch(item.number):
            raise LeadDataError(f"invalid normalised phone number: {item.number!r}")
        unique.setdefault(item.number, item)
    return tuple(unique[number] for number in sorted(unique))


def _social(value: object) -> tuple[SocialLink, ...]:
    links = []
    for item in _items(value, "social_profiles"):
        if not isinstance(item, SocialLink):
            raise LeadDataError(f"social_profiles must contain SocialLink values, got {type(item).__name__}")
        if not item.platform.strip() or not item.url.strip():
            raise LeadDataError("social profile needs a platform and a url")
        links.append(item)
    return dedupe_social_links(links)


def _address(value: object) -> Address | None:
    if value is None:
        return None
    if not isinstance(value, Address):
        raise LeadDataError(f"address must be an Address or None, got {type(value).__name__}")
    parts = {
        key: _text(getattr(value, key), f"address.{key}")
        for key in ("street", "city", "region", "postal_code", "country", "formatted")
    }
    return Address(**parts) if any(parts.values()) else None  # an address without any content is no address


def _page_type(value: object) -> PageCategory | None:
    if value is None or isinstance(value, PageCategory):
        return value
    if isinstance(value, str):
        try:
            return PageCategory(value.strip().lower())
        except ValueError as exc:
            raise LeadDataError(f"unknown page_type: {value!r}") from exc
    raise LeadDataError(f"page_type must be a PageCategory, a string or None, got {type(value).__name__}")


def _moment(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise LeadDataError(f"{name} must be a datetime or None, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise LeadDataError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


# ----------------------------------------------------------------------------- the model
@dataclass(frozen=True, slots=True)
class BusinessLead:
    """One business discovered by the crawler. Only `website` is required."""

    website: str
    business_name: str | None = None
    domain: str | None = None  # derived from `website`; an explicit value must agree with it
    description: str | None = None
    emails: tuple[str, ...] = ()
    phones: tuple[PhoneNumber, ...] = ()
    address: Address | None = None
    social_profiles: tuple[SocialLink, ...] = ()
    page_type: PageCategory | None = None
    language: str | None = None  # "en", "fa", "mixed", "unknown" or another declared code (as ParsedPage.language)
    source_url: str | None = None  # the page this data was found on
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def __post_init__(self) -> None:
        def put(name: str, value: object) -> None:
            object.__setattr__(self, name, value)

        website = normalize_website(self.website)
        host = urlsplit(website).hostname or ""
        derived = domain_of(website)
        domain = _text(self.domain, "domain")
        if domain is not None and domain.lower().rstrip(".") != derived and not same_site(domain, derived):
            # any spelling of the same canonical identity agrees (`WWW.Acme.example`, `https://acme.example/`)
            raise LeadDataError(f"domain {domain!r} does not match website host {host!r} (expected {derived!r})")
        language = _text(self.language, "language")
        first, last = _moment(self.first_seen, "first_seen"), _moment(self.last_seen, "last_seen")
        if first is not None and last is not None and last < first:
            raise LeadDataError("last_seen must not be earlier than first_seen")
        put("website", website)
        put("domain", derived)
        put("business_name", _text(self.business_name, "business_name", collapse=True))
        put("description", _text(self.description, "description"))
        put("emails", _emails(self.emails))
        put("phones", _phones(self.phones))
        put("address", _address(self.address))
        put("social_profiles", _social(self.social_profiles))
        put("page_type", _page_type(self.page_type))
        put("language", language.lower() if language else None)
        put("source_url", _url(self.source_url, "source_url"))
        put("first_seen", first)
        put("last_seen", last)

    # ------------------------------------------------------------------ serialisation
    @property
    def identity(self) -> LeadIdentity:
        """The deterministic identity of this lead (Phase 9.2): its canonical domain, see `app.leads.lead_identity`."""
        return LeadIdentity(self.domain or "")

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict with fixed keys (empty collections stay `[]`, missing values stay `None`)."""
        return {
            "business_name": self.business_name,
            "website": self.website,
            "domain": self.domain,
            "description": self.description,
            "emails": list(self.emails),
            "phones": [{"number": p.number, "raw": p.raw} for p in self.phones],
            "address": self.address.to_dict() if self.address is not None else None,
            "social_profiles": [{"platform": s.platform, "url": s.url} for s in self.social_profiles],
            "page_type": self.page_type.value if self.page_type is not None else None,
            "language": self.language,
            "source_url": self.source_url,
            "first_seen": self.first_seen.isoformat() if self.first_seen is not None else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen is not None else None,
        }

    def to_json(self) -> str:
        """Deterministic JSON text (sorted keys, non-ASCII characters kept as they are)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BusinessLead:
        """Rebuild a lead from `to_dict()` output. Missing optional keys are fine; unknown keys, wrong types
        and invalid values raise `LeadDataError`."""
        if not isinstance(data, Mapping):
            raise LeadDataError(f"lead data must be a mapping, got {type(data).__name__}")
        unknown = sorted(set(data) - set(_FIELDS), key=str)
        if unknown:
            raise LeadDataError(f"unknown lead fields: {', '.join(map(str, unknown))}")
        if "website" not in data:
            raise LeadDataError("website is required")
        return cls(
            website=data["website"],
            business_name=data.get("business_name"),
            domain=data.get("domain"),
            description=data.get("description"),
            emails=_items(data.get("emails"), "emails") if data.get("emails") is not None else (),
            phones=tuple(_phone_from_dict(p) for p in _items(data.get("phones"), "phones")),
            address=_address_from_dict(data.get("address")),
            social_profiles=tuple(_social_from_dict(s) for s in _items(data.get("social_profiles"), "social_profiles")),
            page_type=data.get("page_type"),
            language=data.get("language"),
            source_url=data.get("source_url"),
            first_seen=_moment_from_text(data.get("first_seen"), "first_seen"),
            last_seen=_moment_from_text(data.get("last_seen"), "last_seen"),
        )

    @classmethod
    def from_json(cls, text: str) -> BusinessLead:
        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise LeadDataError(f"invalid lead JSON: {exc}") from exc
        return cls.from_dict(data)

    # ------------------------------------------------------------------ from a parsed page
    @classmethod
    def from_parsed_page(
        cls, parsed: ParsedPage, *, source_url: str, website: str | None = None, seen_at: datetime | None = None,
    ) -> BusinessLead:
        """Map the values already extracted from one page. Nothing is fetched or invented here.

        - `website`, `business_name`, `description`: from `extract_identity` (JSON-LD, Open Graph, title, homepage
          heading; see `app/leads/identity.py`). The name is None when no signal qualifies.
        - `emails` / `phones` / `social_profiles`: the page's own values plus those of its JSON-LD businesses.
        - `address`: the first JSON-LD business address, otherwise the first page address.
        - `page_type` / `language`: the page's classification category and detected language code.
        - `website` defaults to the origin of `source_url`. `seen_at` sets both `first_seen` and `last_seen`
          (no clock is read here; None leaves them unset).
        """
        identity = extract_identity(parsed, source_url=source_url, website=website)
        records = parsed.structured_data
        address = next((a for r in records for a in r.addresses), None) or next(iter(parsed.addresses), None)
        return cls(
            website=identity.website,
            business_name=identity.business_name,
            description=identity.description,
            emails=(*parsed.emails, *(e for r in records for e in r.emails)),
            phones=(*parsed.phones, *(p for r in records for p in r.telephones)),
            address=address,
            social_profiles=(*parsed.social_links, *(s for r in records for s in r.social_links)),
            page_type=parsed.classification.category,
            language=parsed.language.language,
            source_url=source_url,
            first_seen=seen_at,
            last_seen=seen_at,
        )


# ----------------------------------------------------------------------------- deserialisation helpers
def _record(value: object, name: str, keys: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LeadDataError(f"{name} entries must be mappings, got {type(value).__name__}")
    extra = set(value) - set(keys)
    if extra:
        raise LeadDataError(f"unknown {name} fields: {', '.join(sorted(map(str, extra)))}")
    return value


def _phone_from_dict(value: object) -> PhoneNumber:
    record = _record(value, "phone", ("number", "raw"))
    number = _text(record.get("number"), "phone.number")
    if number is None:
        raise LeadDataError("phone.number is required")
    return PhoneNumber(number, _text(record.get("raw"), "phone.raw") or number)


def _social_from_dict(value: object) -> SocialLink:
    record = _record(value, "social profile", ("platform", "url"))
    platform, url = _text(record.get("platform"), "social.platform"), _text(record.get("url"), "social.url")
    if platform is None or url is None:
        raise LeadDataError("social profile needs a platform and a url")
    return SocialLink(platform, url)


def _address_from_dict(value: object) -> Address | None:
    if value is None:
        return None
    record = _record(value, "address", ("street", "city", "region", "postal_code", "country", "formatted"))
    return Address(**{key: record.get(key) for key in record})


def _moment_from_text(value: object, name: str) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value  # type: ignore[return-value]
    if not isinstance(value, str):
        raise LeadDataError(f"{name} must be an ISO 8601 string or None, got {type(value).__name__}")
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise LeadDataError(f"{name} is not an ISO 8601 timestamp: {value!r}") from exc
