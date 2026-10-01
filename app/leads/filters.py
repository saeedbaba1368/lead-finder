"""Deterministic Lead filtering (Phase 10.3). Pure and offline: no network, clock, randomness, scoring, fuzzy search or
enrichment; stored leads are never changed (`BusinessLead` is frozen, the repository is only read).

`LeadFilter` holds criteria on fields a lead already has. All criteria that are set must hold (AND); a criterion with
several values holds when the lead matches any of them (OR). A filter without criteria matches every lead.

| criterion            | accepts                                | a lead matches when                                                    |
|----------------------|----------------------------------------|------------------------------------------------------------------------|
| `domain`             | domain / URL / host, one or many       | its canonical domain (`lead_identity`) equals the canonical value      |
| `language`           | code (`en`, `fa`, `mixed`, ...), many  | its `language` equals it (case-insensitive); no language never matches |
| `page_type`          | `PageCategory` or its name, many       | its `page_type` equals it; no page type never matches                  |
| `country`, `city`    | text, one or many                      | its address country / city equals it; no address never matches         |
| `has_email`          | `True` / `False`                       | it has (True) / has no (False) email                                   |
| `has_phone`          | `True` / `False`                       | it has (True) / has no (False) phone number                            |
| `has_social_profile` | `True` / `False`                       | it has (True) / has no (False) social profile                          |

Matching is exact, not fuzzy. Text criteria (`language`, `country`, `city`) are compared after Unicode NFKC, case folding,
whitespace collapsing and unifying the Arabic letter forms ي/ى/ك with the Persian ی/ک (the same letter written with
another code point), so `Tehran`/`tehran` and `تهران` typed with either yeh match, but `Teheran` does not match `Tehran`.
ZWNJ is kept: `می‌شود` and `میشود` are different texts. Domains use the project's canonical domain identity.

Invalid input raises `LeadFilterError` (a `LeadDataError`) with the offending name and value: unknown criterion names
(`LeadFilter.from_dict`, `filter_leads(..., **criteria)`), blank or non-string text, an empty collection, a non-boolean
`has_*`, an unknown page type, an unusable domain. `None` means "not set"; it is never an error.

Filters compose with `&`: `LeadFilter(language="fa") & LeadFilter(has_email=True)`. A criterion set on both sides keeps
only the values both allow; no common value (or `has_email=True` with `has_email=False`) gives a filter that matches
nothing, never an error. `&` is commutative and associative, and the filter object is immutable and hashable.

Results are ordered by canonical domain (the lead identity), like the exports, so they do not depend on input order, and
`leads_to_json(filter_leads(...))` / `leads_to_csv(...)` export a filtered selection. `filter_stored_leads(repository, ...)`
reads every stored lead (selects only: no flush, no commit) and filters in memory.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.crawler.classify import PageCategory
from app.leads.errors import LeadDataError
from app.leads.lead_identity import lead_identity
from app.leads.model import BusinessLead

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository

TEXT_CRITERIA = ("language", "country", "city")
VALUE_CRITERIA = ("domain", "language", "page_type", "country", "city")
FLAG_CRITERIA = ("has_email", "has_phone", "has_social_profile")
CRITERIA = (*VALUE_CRITERIA, *FLAG_CRITERIA)
_BATCH = 500
_LETTER_FORMS = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"})  # ي ى ك -> ی ک


class LeadFilterError(LeadDataError):
    """An invalid filter (unknown name, bad value). The message names the criterion and the value."""


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_LETTER_FORMS).casefold()
    return " ".join(text.split())


def _values(name: str, raw: object) -> tuple[object, ...]:
    if isinstance(raw, (str, bytes, PageCategory)) or not isinstance(raw, Iterable) or isinstance(raw, Mapping):
        return (raw,)
    items = tuple(raw)
    if not items:
        raise LeadFilterError(f"{name}: an empty list of values would match nothing; leave the criterion out instead")
    return items


def _text(name: str, item: object) -> str:
    if not isinstance(item, str):
        raise LeadFilterError(f"{name}: expected text, got {type(item).__name__} ({item!r})")
    folded = _fold(item)
    if not folded:
        raise LeadFilterError(f"{name}: the value must not be blank")
    return folded


def _domain(name: str, item: object) -> str:
    if not isinstance(item, str) or not item.strip():
        raise LeadFilterError(f"{name}: expected a domain or URL, got {item!r}")
    try:
        return lead_identity(item.strip()).domain
    except LeadDataError as exc:
        raise LeadFilterError(f"{name}: {item!r} is not a usable domain ({exc})") from exc


def _page_type(name: str, item: object) -> str:
    if isinstance(item, PageCategory):
        return item.value
    if isinstance(item, str):
        try:
            return PageCategory(item.strip().lower()).value
        except ValueError:
            pass
    valid = ", ".join(category.value for category in PageCategory)
    raise LeadFilterError(f"{name}: unknown page type {item!r} (valid: {valid})")


def _normalise(name: str, raw: object) -> tuple[str, ...] | None:
    if raw is None:
        return None
    convert = {"domain": _domain, "page_type": _page_type}.get(name, _text)
    return tuple(sorted({convert(name, item) for item in _values(name, raw)}))


def _flag(name: str, raw: object) -> bool | None:
    if raw is None or isinstance(raw, bool):
        return raw
    raise LeadFilterError(f"{name}: expected True or False, got {type(raw).__name__} ({raw!r})")


@dataclass(frozen=True, slots=True)
class LeadFilter:
    """Criteria a lead must satisfy (module docstring). Every argument is optional; none = match every lead."""

    domain: Any = None
    language: Any = None
    page_type: Any = None
    country: Any = None
    city: Any = None
    has_email: bool | None = None
    has_phone: bool | None = None
    has_social_profile: bool | None = None
    never: bool = field(default=False, repr=False)  # set by `&` when the combined criteria exclude every lead

    def __post_init__(self) -> None:
        for name in VALUE_CRITERIA:
            object.__setattr__(self, name, _normalise(name, getattr(self, name)))
        for name in FLAG_CRITERIA:
            object.__setattr__(self, name, _flag(name, getattr(self, name)))
        if not isinstance(self.never, bool):
            raise LeadFilterError(f"never: expected True or False, got {self.never!r}")

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> LeadFilter:
        """Build a filter from criteria by name. An unknown name raises `LeadFilterError` listing the valid ones."""
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise LeadFilterError(f"filter criteria must be a mapping, got {type(data).__name__}")
        unknown = sorted(map(str, set(data) - set(CRITERIA)))
        if unknown:
            raise LeadFilterError(f"unknown filter criteria: {', '.join(unknown)} (valid: {', '.join(CRITERIA)})")
        return cls(**data)

    @property
    def is_empty(self) -> bool:
        """True if the filter has no criteria (it matches every lead)."""
        return not self.never and all(getattr(self, name) is None for name in CRITERIA)

    def to_dict(self) -> dict[str, Any]:
        """The normalised criteria that are set, in fixed order (value criteria as sorted lists)."""
        data: dict[str, Any] = {}
        for name in CRITERIA:
            value = getattr(self, name)
            if value is not None:
                data[name] = list(value) if isinstance(value, tuple) else value
        if self.never:
            data["never"] = True
        return data

    # ------------------------------------------------------------------ matching
    def matches(self, lead: BusinessLead) -> bool:
        if not isinstance(lead, BusinessLead):
            raise LeadFilterError(f"can only filter BusinessLead values, got {type(lead).__name__}")
        if self.never:
            return False
        address = lead.address
        checks = (
            (self.domain, lead.domain),
            (self.language, _fold(lead.language) if lead.language else None),
            (self.page_type, lead.page_type.value if lead.page_type is not None else None),
            (self.country, _fold(address.country) if address is not None and address.country else None),
            (self.city, _fold(address.city) if address is not None and address.city else None),
        )
        if any(allowed is not None and (actual is None or actual not in allowed) for allowed, actual in checks):
            return False
        flags = (
            (self.has_email, bool(lead.emails)), (self.has_phone, bool(lead.phones)),
            (self.has_social_profile, bool(lead.social_profiles)),
        )
        return all(wanted is None or wanted == actual for wanted, actual in flags)

    # ------------------------------------------------------------------ composition
    def __and__(self, other: object) -> LeadFilter:
        if not isinstance(other, LeadFilter):
            return NotImplemented
        merged: dict[str, Any] = {}
        never = self.never or other.never
        for name in CRITERIA:
            a, b = getattr(self, name), getattr(other, name)
            if a is None or b is None:
                merged[name] = b if a is None else a
            elif name in FLAG_CRITERIA:
                merged[name] = a if a == b else None
                never = never or a != b
            else:
                common = tuple(sorted(set(a) & set(b)))
                merged[name] = common or None
                never = never or not common
        return LeadFilter(**merged, never=never)


# ----------------------------------------------------------------------------- applying filters
def _resolve(flt: LeadFilter | None, criteria: Mapping[str, Any]) -> LeadFilter:
    if flt is not None and not isinstance(flt, LeadFilter):
        raise LeadFilterError(f"filter must be a LeadFilter, got {type(flt).__name__}")
    extra = LeadFilter.from_dict(criteria)
    return extra if flt is None else flt & extra


def filter_leads(leads: Iterable[BusinessLead], flt: LeadFilter | None = None, **criteria: Any) -> list[BusinessLead]:
    """The leads that match, ordered by canonical domain. `filter_leads(leads)` returns all of them (sorted).

    Criteria may be given as a `LeadFilter`, as keyword arguments, or both (combined with AND)."""
    selected = _resolve(flt, criteria)
    items = list(leads)
    for item in items:
        if not isinstance(item, BusinessLead):
            raise LeadFilterError(f"can only filter BusinessLead values, got {type(item).__name__}")
    return sorted((lead for lead in items if selected.matches(lead)), key=lambda lead: lead.identity.key)


def filter_stored_leads(repository: LeadRepository, flt: LeadFilter | None = None, **criteria: Any) -> list[BusinessLead]:
    """`filter_leads` over every stored lead. Read-only. A bad filter fails before the database is read."""
    selected = _resolve(flt, criteria)
    leads: list[BusinessLead] = []
    offset = 0
    while True:
        batch = repository.load_all(limit=_BATCH, offset=offset)
        leads.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
    return filter_leads(leads, selected)
