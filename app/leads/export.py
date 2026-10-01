"""Lead JSON export (Phase 10.1). Read-only, offline, deterministic. JSON only: no CSV, filtering or CLI here.

Document (UTF-8 text, 2-space indent, one trailing newline, non-ASCII characters written as they are):

    {"version": 1, "count": <n>, "leads": [<lead>, ...]}

* `<lead>` is exactly `BusinessLead.to_dict()` (the project's own serialisation): the same fixed keys in the same
  order for every lead, `null` for a missing optional value, `[]` for an empty collection, ISO 8601 UTC timestamps.
  `BusinessLead.from_dict(entry)` rebuilds the lead, so an export can be read back with existing code.
* Order: by canonical domain (the lead identity), a total order because there is one lead per domain. It does not
  depend on insertion order, on the database, or on the order of the input.
* Same leads in, same bytes out. Nothing in the document depends on the clock, the host or the process.
* An empty set gives `{"version": 1, "count": 0, "leads": []}` (valid JSON, never an empty file).

`leads_to_json(leads)` / `lead_to_json(lead)` work on `BusinessLead` values. `export_stored_leads(repository)` reads every
stored lead through `LeadRepository.load_all` (selects only: no flush, no commit, no change to rows or `updated_at`).
`write_leads_json(path, leads)` writes the document as UTF-8 (no BOM) via a temporary file in the same directory, so a
failure never leaves a half-written target. Duplicated identities in the input raise `LeadDataError` (exports are
never silently de-duplicated; use `deduplicate_leads` first).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.leads.errors import LeadDataError
from app.leads.model import BusinessLead

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.lead import LeadRepository

EXPORT_VERSION = 1
_BATCH = 500


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def lead_to_json(lead: BusinessLead) -> str:
    """One lead as an indented JSON object (`to_dict()` key order)."""
    if not isinstance(lead, BusinessLead):
        raise LeadDataError(f"expected a BusinessLead, got {type(lead).__name__}")
    return _dump(lead.to_dict())


def leads_document(leads: Iterable[BusinessLead], *, preserve_order: bool = False) -> dict[str, Any]:
    """The export document as a plain dict (see the module docstring).

    `preserve_order=True` (Phase 10.5) keeps the given order instead of sorting by domain, so a sorted query result is
    exported in its query order. The default is unchanged."""
    items = list(leads)
    for item in items:
        if not isinstance(item, BusinessLead):
            raise LeadDataError(f"leads must contain BusinessLead values, got {type(item).__name__}")
    ordered = items if preserve_order else sorted(items, key=lambda lead: lead.identity.key)
    keys = [lead.identity.key for lead in ordered]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise LeadDataError(f"cannot export two leads with the same identity: {', '.join(duplicated)}")
    return {"version": EXPORT_VERSION, "count": len(ordered), "leads": [lead.to_dict() for lead in ordered]}


def leads_to_json(leads: Iterable[BusinessLead], *, preserve_order: bool = False) -> str:
    """One or many leads as the export document text (domain order unless `preserve_order`)."""
    return _dump(leads_document(leads, preserve_order=preserve_order))


def export_stored_leads(repository: LeadRepository) -> str:
    """The export document of every stored lead. Read-only."""
    leads: list[BusinessLead] = []
    offset = 0
    while True:
        batch = repository.load_all(limit=_BATCH, offset=offset)
        leads.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
    return leads_to_json(leads)


def write_leads_json(path: str | os.PathLike[str], leads: Iterable[BusinessLead], *, preserve_order: bool = False) -> Path:
    """Write the export document of `leads` to `path` as UTF-8 and return the path."""
    target = Path(path)
    data = leads_to_json(leads, preserve_order=preserve_order).encode("utf-8")  # built (and validated) before anything is written
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return target
