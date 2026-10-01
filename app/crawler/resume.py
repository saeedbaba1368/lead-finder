"""Crawl resume support (Phase 6.3.2.2.2B): rebuild BFS state from the persisted `Page` rows.

The persisted rows *are* the state. Status meaning while a crawl is persisted:

* ``FETCHED`` / ``FAILED`` - the URL was requested and its outcome recorded (never requested again);
* ``PENDING``              - discovered and queued, not yet requested (the BFS frontier);
* ``SKIPPED``              - discovered but not queued because ``max_pages`` was reached.

Nothing here performs I/O other than reading (and promoting ``SKIPPED`` rows via) the repository.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models import Page, PageStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.repositories.page import PageRepository

_BATCH = 500
_ATTEMPTED = (PageStatus.FETCHED, PageStatus.FAILED)


class CrawlPersistenceError(RuntimeError):
    """A crawl-state write/read failed. The failing operation was rolled back (no partial or false
    visited state); `result` holds what the crawl had completed in memory, when available."""

    result: object | None = None


@dataclass
class ResumeState:
    queued: list[str] = field(default_factory=list)  # every URL ever queued, in queue order
    known: list[str] = field(default_factory=list)  # every persisted URL (for the UrlGate replay)
    visited: list[str] = field(default_factory=list)  # URLs requested by earlier runs
    done: set[str] = field(default_factory=set)  # requested URLs + normalised redirect targets
    attempted: int = 0  # URLs already requested (counts towards max_pages)
    deepest_level: int = 0
    start_depth: int = 0
    level: list[tuple[str, str | None]] = field(default_factory=list)  # frontier at start_depth
    later: dict[int, list[tuple[str, str | None]]] = field(default_factory=dict)  # deeper frontier levels
    skipped_left: int = 0  # still skipped because of max_pages


def _all_pages(repo: PageRepository, crawl_id: int) -> list[Page]:
    rows: list[Page] = []
    while batch := repo.list_for_crawl(crawl_id, limit=_BATCH, offset=len(rows)):
        rows.extend(batch)
    return rows


def load_resume_state(
    repo: PageRepository, crawl_id: int, *, max_pages: int, normalize: Callable[[str], str],
    promote: bool = True,
) -> ResumeState | None:
    """Return the persisted BFS state of `crawl_id`, or None when it has no pages (fresh crawl).

    ``SKIPPED`` rows are promoted back to ``PENDING`` (in discovery order) while the queue has room
    under `max_pages`, so a crawl stopped by `max_pages` continues once the limit is raised.
    `promote=False` makes the call strictly read-only (used for crawls that are already finished).
    """
    rows = _all_pages(repo, crawl_id)
    if not rows:
        return None
    state = ResumeState(known=[p.url for p in rows])
    pending: list[Page] = []
    skipped: list[Page] = []
    for p in rows:
        if p.status in _ATTEMPTED:
            state.done.add(p.url)
            state.visited.append(p.url)
            state.attempted += 1
            state.deepest_level = max(state.deepest_level, p.depth)
            if p.final_url:
                state.done.add(normalize(p.final_url))
        elif p.status is PageStatus.PENDING:
            pending.append(p)
        else:
            skipped.append(p)
    room = (max_pages - state.attempted - len(pending)) if promote else 0
    for p in skipped[: max(room, 0)]:
        repo.update(p, status=PageStatus.PENDING)
        pending.append(p)
    state.skipped_left = len(skipped) - max(min(room, len(skipped)), 0)
    queued_rows = sorted([p for p in rows if p.status in _ATTEMPTED] + pending, key=lambda p: p.id)
    state.queued = [p.url for p in queued_rows]
    by_depth: dict[int, list[Page]] = {}
    for p in sorted(pending, key=lambda p: p.id):
        if p.url in state.done:  # already fetched as another URL's redirect target
            continue
        by_depth.setdefault(p.depth, []).append(p)
    frontier = {d: [(p.url, p.parent_url) for p in ps] for d, ps in by_depth.items()}
    if frontier:
        state.start_depth = min(frontier)
        state.level = frontier.pop(state.start_depth)
        state.later = frontier
    return state
