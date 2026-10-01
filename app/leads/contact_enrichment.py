"""Phase 12.2: contact-data enrichment (emails and phones).

Pure functions, no I/O, no parsing. They take values that the existing
extractors already produced and merge them into a lead's contact lists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}$")


def normalize_email(value: str | None) -> str | None:
    """Lowercase, strip 'mailto:', query string and punctuation. None if invalid."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v.startswith("mailto:"):
        v = v[len("mailto:"):]
    v = v.split("?", 1)[0].strip().strip(".,;:<>()[]\"'")
    return v if _EMAIL_RE.match(v) else None


def normalize_phone(value: str | None) -> str | None:
    """Keep digits and a leading '+'. Drop '00' prefix. None unless 7-15 digits."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.lower().startswith("tel:"):
        v = v[4:]
    plus = v.startswith("+")
    digits = re.sub(r"\D", "", v)
    if not plus and digits.startswith("00"):
        digits, plus = digits[2:], True
    if not 7 <= len(digits) <= 15:
        return None
    return ("+" if plus else "") + digits


def _merge(existing: Iterable[str] | None, new: Iterable[str] | None, norm) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in list(existing or []) + list(new or []):
        n = norm(item)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def merge_emails(existing: Iterable[str] | None, new: Iterable[str] | None) -> list[str]:
    """Existing values first (preserved), then new ones. Normalised, de-duplicated."""
    return _merge(existing, new, normalize_email)


def merge_phones(existing: Iterable[str] | None, new: Iterable[str] | None) -> list[str]:
    return _merge(existing, new, normalize_phone)


def enrich_contacts(
    existing_emails: Iterable[str] | None,
    existing_phones: Iterable[str] | None,
    page_emails: Iterable[str] | None,
    page_phones: Iterable[str] | None,
) -> tuple[list[str], list[str]]:
    """Merge one page's contacts into a lead's contacts. Never loses valid data."""
    return (
        merge_emails(existing_emails, page_emails),
        merge_phones(existing_phones, page_phones),
    )
