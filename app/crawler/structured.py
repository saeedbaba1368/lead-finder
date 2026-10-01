"""Structured business information (Phase 7.2.4): JSON-LD and addresses. Pure: no network, never raises.

`parse_jsonld(blocks)` turns the text of `<script type="application/ld+json">` elements into
`StructuredBusiness` records for Organization, LocalBusiness, ProfessionalService, Corporation and Person
nodes (top-level objects, arrays and `@graph` members). Anything else in the JSON, and malformed JSON, is
skipped. Telephones reuse `PhoneNumber`, e-mails the 7.2.1 validation, and `sameAs` profile URLs the 7.2.3
`SocialLink` classifier. These values stay inside the structured record: the page-level `emails`, `phones`
and `social_links` keep coming only from visible text and links, exactly as before.

`Address` is shared by schema.org `address` data and `<address>` elements (collected by the parser).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.crawler.social import SocialLink, classify_social_url, dedupe_social_links

if TYPE_CHECKING:  # runtime import is lazy (html_parser imports this module)
    from app.crawler.html_parser import PhoneNumber

logger = get_logger(__name__)

RECOGNIZED_TYPES = ("ProfessionalService", "LocalBusiness", "Corporation", "Organization", "Person")  # most specific first
_TYPE_LOOKUP = {name.lower(): name for name in RECOGNIZED_TYPES}

MAX_JSONLD_BLOCKS = 50
MAX_JSONLD_CHARS = 1_000_000
MAX_ENTITIES = 100
MAX_ADDRESSES = 100
_MAX_DEPTH = 6  # nesting followed while looking for typed nodes (arrays / @graph)
_MAX_NODES = 2000
_MAX_ITEMS = 50  # values kept per list field
_MAX_TEXT = 2000


# ----------------------------------------------------------------------------- model
@dataclass(frozen=True, slots=True)
class Address:
    street: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None
    formatted: str | None = None  # one-line form: composed from the parts, or the text as written

    def to_dict(self) -> dict[str, str | None]:
        return {
            "street": self.street, "city": self.city, "region": self.region,
            "postal_code": self.postal_code, "country": self.country, "formatted": self.formatted,
        }


@dataclass(frozen=True, slots=True)
class StructuredBusiness:
    type: str  # one of RECOGNIZED_TYPES
    name: str | None = None
    url: str | None = None  # as written, not resolved or normalised
    description: str | None = None
    telephones: tuple[PhoneNumber, ...] = ()  # sorted by normalised number
    emails: tuple[str, ...] = ()  # lower-case, sorted
    addresses: tuple[Address, ...] = ()
    same_as: tuple[str, ...] = ()  # sorted, unique
    social_links: tuple[SocialLink, ...] = ()  # the sameAs entries that are social profiles
    logo: str | None = None  # URL as written

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type, "name": self.name, "url": self.url, "description": self.description,
            "telephones": [{"number": p.number, "raw": p.raw} for p in self.telephones],
            "emails": list(self.emails),
            "addresses": [a.to_dict() for a in self.addresses],
            "same_as": list(self.same_as),
            "social_links": [{"platform": s.platform, "url": s.url} for s in self.social_links],
            "logo": self.logo,
        }


# ----------------------------------------------------------------------------- value helpers
def _text(value: Any) -> str | None:
    """A whitespace-normalised string from a JSON value (string, int, `{"@value": ...}`, or first of a list)."""
    if isinstance(value, str):
        return " ".join(value.split())[:_MAX_TEXT] or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        return _text(value.get("@value"))
    if isinstance(value, list):
        for item in value[:_MAX_ITEMS]:
            if (text := _text(item)) is not None:
                return text
    return None


def _strings(value: Any) -> list[str]:
    """Every usable string in a value that may be a scalar or a list."""
    items = value[:_MAX_ITEMS] if isinstance(value, list) else [value]
    return [text for item in items if (text := _text(item)) is not None]


def _phones(value: Any) -> list[PhoneNumber]:
    from app.crawler import html_parser as hp  # lazy: html_parser imports this module

    found = []
    for raw in _strings(value):
        phone = hp._phone_from_tel("tel:" + raw)  # the value is explicitly a phone number
        if phone is None:  # e.g. "Phone: +1 555 123 4567 ext 12"
            candidates = hp.extract_phones_from_text(raw)
            phone = candidates[0] if candidates else None
        if phone is not None:
            found.append(phone)
    return found


def _emails(value: Any) -> list[str]:
    from app.crawler import html_parser as hp

    found = []
    for raw in _strings(value):
        if raw[:7].lower() == "mailto:":
            raw = raw[7:].split("?", 1)[0]
        found.extend(hp.extract_emails_from_text(raw))
    return found


def _image_url(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("url")) or _text(value.get("contentUrl"))
    if isinstance(value, list):
        for item in value[:_MAX_ITEMS]:
            if (url := _image_url(item)) is not None:
                return url
        return None
    return _text(value)


def _country(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


def _postal_address(node: dict[str, Any]) -> Address | None:
    street, city, region = _text(node.get("streetAddress")), _text(node.get("addressLocality")), _text(node.get("addressRegion"))
    postal_code, country = _text(node.get("postalCode")), _country(node.get("addressCountry"))
    parts = [part for part in (street, city, region, postal_code, country) if part]
    if not parts:
        return None
    return Address(street, city, region, postal_code, country, ", ".join(parts))


def _addresses(value: Any) -> list[Address]:
    found = []
    for item in value[:_MAX_ITEMS] if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            address = _postal_address(item)
        elif isinstance(item, str) and (text := _text(item)) is not None:
            address = Address(formatted=text)  # free-text address: only the one-line form is known
        else:
            address = None
        if address is not None:
            found.append(address)
    return found


def _address_key(address: Address) -> tuple[str, ...]:
    return tuple((part or "").casefold() for part in (address.street, address.city, address.region, address.postal_code, address.country, address.formatted))


def unique_addresses(addresses: Iterable[Address]) -> tuple[Address, ...]:
    """De-duplicated (case-insensitive), sorted deterministically; the first one seen is kept."""
    unique: dict[tuple[str, ...], Address] = {}
    for address in addresses:
        unique.setdefault(_address_key(address), address)
    return tuple(unique[key] for key in sorted(unique))[:MAX_ADDRESSES]


# ----------------------------------------------------------------------------- JSON-LD
def _recognized_type(value: Any) -> str | None:
    kinds = value if isinstance(value, list) else [value]
    found = {
        _TYPE_LOOKUP[name]
        for kind in kinds
        if isinstance(kind, str) and (name := kind.strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()) in _TYPE_LOOKUP
    }
    return next((name for name in RECOGNIZED_TYPES if name in found), None)


def _contact_points(node: dict[str, Any]) -> list[dict[str, Any]]:
    points = node.get("contactPoint")
    items = points if isinstance(points, list) else [points]
    return [point for point in items[:10] if isinstance(point, dict)]


def _entity(node: dict[str, Any]) -> StructuredBusiness | None:
    kind = _recognized_type(node.get("@type"))
    if kind is None:
        return None
    phones: dict[str, PhoneNumber] = {}
    emails: set[str] = set()
    for source in (node, *_contact_points(node)):  # contactPoint is where most sites put phone and e-mail
        for phone in _phones(source.get("telephone")):
            phones.setdefault(phone.number, phone)
        emails.update(_emails(source.get("email")))
    same_as = sorted({url for url in _strings(node.get("sameAs")) if url[:4].lower() == "http"})[: _MAX_ITEMS * 2]
    entity = StructuredBusiness(
        type=kind,
        name=_text(node.get("name")),
        url=_text(node.get("url")),
        description=_text(node.get("description")),
        telephones=tuple(phones[number] for number in sorted(phones))[:_MAX_ITEMS],
        emails=tuple(sorted(emails))[:_MAX_ITEMS],
        addresses=unique_addresses(_addresses(node.get("address"))),
        same_as=tuple(same_as),
        social_links=dedupe_social_links(link for url in same_as if (link := classify_social_url(url)) is not None),
        logo=_image_url(node.get("logo")),
    )
    has_data = any((entity.name, entity.url, entity.description, entity.telephones, entity.emails, entity.addresses, entity.same_as, entity.logo))
    return entity if has_data else None


def _typed_nodes(data: Any) -> list[dict[str, Any]]:
    """Dict nodes of a JSON-LD document: the object itself, array members and `@graph` members (bounded)."""
    nodes: list[dict[str, Any]] = []
    budget = [_MAX_NODES]

    def walk(value: Any, depth: int) -> None:
        if depth > _MAX_DEPTH or budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, dict):
            nodes.append(value)
            if "@graph" in value:
                walk(value["@graph"], depth + 1)

    walk(data, 0)
    return nodes


_WRAPPER_START = re.compile(r"^\s*(?:<!--|<!\[CDATA\[)")
_WRAPPER_END = re.compile(r"(?:-->|\]\]>)\s*$")


def _load(block: str) -> Any | None:
    if len(block) > MAX_JSONLD_CHARS:
        logger.debug("jsonld_too_large", extra={"size": len(block)})
        return None
    text = _WRAPPER_END.sub("", _WRAPPER_START.sub("", block.lstrip("\ufeff"))).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:  # malformed or absurdly nested JSON: skip the block
        logger.debug("jsonld_malformed", extra={"error": repr(exc)[:200]})
        return None


def _merge(first: StructuredBusiness, second: StructuredBusiness) -> StructuredBusiness:
    phones = {p.number: p for p in (*first.telephones, *second.telephones)}
    return StructuredBusiness(
        type=first.type,
        name=first.name or second.name,
        url=first.url or second.url,
        description=first.description or second.description,
        telephones=tuple(phones[number] for number in sorted(phones))[:_MAX_ITEMS],
        emails=tuple(sorted({*first.emails, *second.emails}))[:_MAX_ITEMS],
        addresses=unique_addresses((*first.addresses, *second.addresses)),
        same_as=tuple(sorted({*first.same_as, *second.same_as}))[: _MAX_ITEMS * 2],
        social_links=dedupe_social_links((*first.social_links, *second.social_links)),
        logo=first.logo or second.logo,
    )


def _identity(entity: StructuredBusiness) -> tuple[str, ...]:
    if entity.name or entity.url:  # the same thing described twice, possibly with different detail
        return (entity.type, (entity.name or "").casefold(), (entity.url or "").lower().rstrip("/"))
    return (entity.type, repr(entity))


def parse_jsonld(blocks: Iterable[str]) -> tuple[StructuredBusiness, ...]:
    """Structured business records from JSON-LD script texts: merged when the same entity repeats, sorted."""
    merged: dict[tuple[str, ...], StructuredBusiness] = {}
    for block in list(blocks)[:MAX_JSONLD_BLOCKS]:
        data = _load(block)
        if data is None:
            continue
        for node in _typed_nodes(data):
            entity = _entity(node)
            if entity is None:
                continue
            key = _identity(entity)
            merged[key] = _merge(merged[key], entity) if key in merged else entity
    ordered = sorted(merged.values(), key=lambda e: (e.type, (e.name or "").casefold(), (e.url or "").lower(), repr(e)))
    return tuple(ordered[:MAX_ENTITIES])
