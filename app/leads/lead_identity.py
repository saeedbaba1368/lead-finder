"""Deterministic Lead identity (Phase 9.2). Pure: no network, clock, randomness, configuration or database access.

A lead is *one business site*, so its identity is the canonical domain of its website (`app.urls.domain_identity`,
Phase 9.1). `lead_identity(source)` returns a `LeadIdentity` with three fields, all derived from that domain:

* `domain`: the canonical domain. This is exactly what persistence already stores (`domains.name`, the unique key of
  `leads.domain_id`), so no column, migration or repository change is needed and a stored lead is found by
  `LeadRepository.get_by_domain(identity.domain)`.
* `key`: `lead:v1:<domain>`, a namespaced, versioned string for use as a dictionary / log / external key.
* `digest`: the SHA-256 hex digest of `key` encoded as UTF-8, a fixed-length ASCII id.

Properties:

* Deterministic and stable across processes and restarts: no `hash()`, no ordering, no time, no randomness.
* Independent of page order and of which page the data came from: only the website's domain takes part, never a
  path, query, scheme, port, `www.` or any lead content (name, emails, ...).
* Unicode-safe: IDN domains are punycode (ASCII) by construction; the digest is taken over UTF-8 bytes.
* Serializable: `to_dict` / `to_json` / `from_dict` / `from_json`. Reading back re-derives `key` and `digest` and
  refuses any value that does not match (a tampered or stale identity is an error, never silently repaired).
* Exact: no fuzzy matching. Two sites have the same identity only if their canonical domains are identical strings.

`source` may be a `BusinessLead`, a `BusinessIdentity`, a `DomainIdentity`, or a string (URL, scheme-less URL or bare
host). Anything unusable raises `LeadDataError`.

Not here, on purpose: merging or de-duplicating leads, fuzzy / name matching, lookups, persistence changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.leads.errors import LeadDataError
from app.urls.domain_identity import DomainIdentity, canonical_domain
from app.urls.errors import UrlRejected

IDENTITY_VERSION = 1
KEY_PREFIX = f"lead:v{IDENTITY_VERSION}:"
_FIELDS = frozenset({"version", "domain", "key", "digest"})


def identity_key(domain: str) -> str:
    return f"{KEY_PREFIX}{domain}"


def identity_digest(domain: str) -> str:
    return hashlib.sha256(identity_key(domain).encode("utf-8")).hexdigest()


def _canonical(domain: object) -> str:
    """`domain` if it is already a canonical domain, else `LeadDataError` (strict: never repairs)."""
    if not isinstance(domain, str) or not domain:
        raise LeadDataError(f"lead identity domain must be a non-empty string, got {domain!r}")
    try:
        canonical = canonical_domain(domain)
    except UrlRejected as exc:
        raise LeadDataError(f"lead identity domain {domain!r} is not a usable domain: {exc}") from exc
    if canonical != domain:
        raise LeadDataError(f"lead identity domain {domain!r} is not canonical (expected {canonical!r})")
    return domain


@dataclass(frozen=True, slots=True)
class LeadIdentity:
    """The identity of a lead. Construct it with `lead_identity(...)`; direct construction takes only a canonical
    domain (anything else raises `LeadDataError`) and derives `key` and `digest` itself."""

    domain: str
    key: str = field(init=False)
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        domain = _canonical(self.domain)
        object.__setattr__(self, "key", identity_key(domain))
        object.__setattr__(self, "digest", identity_digest(domain))

    @property
    def version(self) -> int:
        return IDENTITY_VERSION

    def __str__(self) -> str:
        return self.key

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "domain": self.domain, "key": self.key, "digest": self.digest}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: object) -> LeadIdentity:
        if not isinstance(data, dict):
            raise LeadDataError(f"lead identity must be a mapping, got {type(data).__name__}")
        unknown, missing = sorted(set(data) - _FIELDS, key=str), sorted(_FIELDS - set(data))
        if unknown or missing:
            raise LeadDataError(f"lead identity fields do not match (unknown: {unknown}, missing: {missing})")
        if data["version"] != IDENTITY_VERSION or isinstance(data["version"], bool):
            raise LeadDataError(f"unsupported lead identity version: {data['version']!r}")
        identity = cls(data["domain"])
        if data["key"] != identity.key or data["digest"] != identity.digest:
            raise LeadDataError("lead identity key/digest do not match its domain")
        return identity

    @classmethod
    def from_json(cls, text: object) -> LeadIdentity:
        if not isinstance(text, str):
            raise LeadDataError(f"lead identity JSON must be a string, got {type(text).__name__}")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise LeadDataError(f"invalid lead identity JSON: {exc}") from exc
        return cls.from_dict(data)


def lead_identity(source: object) -> LeadIdentity:
    """The identity of the lead described by `source` (see the module docstring). Raises `LeadDataError`."""
    if isinstance(source, LeadIdentity):
        return source
    if isinstance(source, DomainIdentity):
        return LeadIdentity(source.domain)
    if isinstance(source, str):
        try:
            return LeadIdentity(canonical_domain(source))
        except UrlRejected as exc:
            raise LeadDataError(f"cannot derive a lead identity from {source!r}: {exc}") from exc
    domain = getattr(source, "domain", None)  # BusinessLead / BusinessIdentity: already canonical by construction
    if isinstance(domain, str) and domain:
        return LeadIdentity(domain)
    raise LeadDataError(f"cannot derive a lead identity from {type(source).__name__}")
