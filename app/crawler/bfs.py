"""Breadth-first internal crawler (Phase 6.2), built on the Phase 6.1 `AsyncHttpClient`.

Level by level: depth 0 is the seed, depth 1 its links, and so on. Discovered links go through
`UrlGate` (normalisation, scope, SSRF, extension and trap policy, duplicate suppression), so
each normalised URL is queued at most once and external domains are never followed.

Phase 6.3.1 adds crawl controls: `max_crawl_time` (a budget on the event loop's monotonic clock,
checked before every request, which also bounds the request in flight) and `request_delay` (minimum seconds between
request starts, enforced with an asyncio limiter). Requests are still sequential.

Phase 6.3.2.2.1 adds an optional persistence hook: with a `PageRepository` and a `crawl_id`, every
completed fetch is stored as a `Page` row (the caller owns the transaction).

Phase 6.3.2.2.2 adds resume: every queued URL is stored as a PENDING page (URLs left out by `max_pages`
as SKIPPED), so a later `crawl()` with the same `crawl_id` reloads the visited set and the frontier and
never requests a stored URL again (see `app/crawler/resume.py`). With a `CrawlRepository` the crawl row
also records progress, the stop reason and the elapsed time. `max_crawl_time` applies per run.

Phase 7.1 adds HTML content parsing (`app/crawler/html_parser.py`): every page fetched OK as HTML is parsed into a
`ParsedPage` (title, visible text, headings, raw links, metadata) that is attached to its `CrawledPage`; the page
title is stored in the existing `pages.title` column. A parser failure is logged and leaves `parsed=None`; it never
stops the crawl. Link discovery still uses `extract_links` and `UrlGate`, unchanged.

Phase 8.5 adds an optional `page_observer(page, crawl_id)` hook. It is called for every completed fetch, inside
the same SAVEPOINT as the page write, so whatever it stores (the lead, see `app/leads/pipeline.py`) is committed or
rolled back together with the page row that produced it. Without an observer nothing changes. An observer error that
is not a database error is logged and rolled back for that page only; it never stops the crawl.

Not implemented (later phases): JavaScript rendering, advanced extraction.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.crawler.http_client import AsyncHttpClient, HttpClientConfig
from app.crawler.html_parser import ParsedPage, parse_response
from app.crawler.links import extract_links
from app.crawler.models import PageResult
from app.crawler.ratelimit import RequestRateLimiter
from app.crawler.lifecycle import CrawlLifecycle, CrawlTerminalError
from app.crawler.resume import CrawlPersistenceError, load_resume_state
from app.db.base import utcnow
from app.models import CrawlStatus, CrawlStopReason, Page, PageOutcome, PageStatus
from app.urls.content import HTML_MEDIA_TYPES
from app.urls.engine import UrlEvaluator, UrlGate
from app.urls.errors import UrlRejected
from app.urls.policy import UrlPolicy

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings
    from app.repositories.crawl import CrawlRepository
    from app.repositories.page import PageRepository
    from app.urls.ssrf import Resolver

logger = get_logger(__name__)


class StopReason(StrEnum):
    COMPLETED = "completed"  # frontier exhausted (possibly with max_depth pruning, see depth_limited)
    MAX_PAGES = "max_pages"  # page limit reached with URLs still unfetched or skipped
    MAX_CRAWL_TIME = "max_crawl_time"  # time budget used up with URLs still unfetched (Phase 6.3.1)
    SEED_REJECTED = "seed_rejected"  # the seed URL failed the URL policy; nothing was fetched
    CANCELLED = "cancelled"  # cancel() was called (Phase 6.4); the in-flight page was finished first


@dataclass(frozen=True, slots=True)
class CrawledPage:
    url: str  # normalised URL that was requested
    depth: int
    parent: str | None  # URL of the page the link was found on (None for the seed)
    result: PageResult  # the Phase 6.1 result, unchanged
    links_found: int = 0  # candidate hrefs extracted
    links_queued: int = 0  # of those, newly queued
    parsed: ParsedPage | None = None  # Phase 7.1: parsed HTML (None: not HTML, not OK, empty, or the parser failed)

    @property
    def ok(self) -> bool:
        return self.result.ok


PageObserver = Callable[[CrawledPage, int], None]  # (completed page, crawl_id); runs inside the page's SAVEPOINT


@dataclass
class CrawlResult:
    seed: str
    max_pages: int
    max_depth: int
    pages: list[CrawledPage] = field(default_factory=list)  # every fetch attempt, in BFS order
    queued: list[str] = field(default_factory=list)  # every URL ever queued, in queue order
    current_depth: int = 0  # depth of the level being processed when the crawl stopped
    deepest_level: int = 0  # deepest level actually fetched
    stop_reason: StopReason = StopReason.COMPLETED
    depth_limited: bool = False  # links were found on pages at max_depth and not followed
    skipped_by_depth: int = 0
    skipped_by_max_pages: int = 0
    redirect_duplicates: list[str] = field(default_factory=list)  # queued URLs already fetched as a redirect target
    rejections: Counter[str] = field(default_factory=Counter)  # URL-policy reject reasons
    seed_rejection: str = ""
    max_crawl_time: float | None = None  # configured budget in seconds (None = unlimited)
    request_delay: float = 0.0  # configured minimum seconds between request starts
    elapsed: float = 0.0  # seconds the crawl ran
    time_limited: bool = False  # the crawl stopped because max_crawl_time was reached
    previously_visited: list[str] = field(default_factory=list)  # requested by earlier runs (resumed crawl)

    @property
    def visited(self) -> list[str]:
        return [p.url for p in self.pages]

    @property
    def fetched(self) -> list[str]:
        """URLs fetched successfully (outcome OK)."""
        return [p.url for p in self.pages if p.ok]

    @property
    def failed(self) -> list[str]:
        """URLs whose fetch did not end OK (HTTP error, timeout, unsupported type, ...)."""
        return [p.url for p in self.pages if not p.ok]

    @property
    def pending(self) -> list[str]:
        """Queued but never fetched (non-empty when the crawl stopped on max_pages or max_crawl_time)."""
        done = set(self.visited) | set(self.redirect_duplicates) | set(self.previously_visited)
        return [u for u in self.queued if u not in done]


class BfsCrawler:
    """Crawl one site breadth-first. `crawl()` never raises for per-URL problems.

    `client` and `evaluator` should share the same `UrlEvaluator` so redirects are also
    scope-checked by the client (`build_crawler` does this).
    """

    def __init__(
        self,
        client: AsyncHttpClient,
        evaluator: UrlEvaluator,
        *,
        max_pages: int = 100,
        max_depth: int = 3,
        max_crawl_time: float | None = None,
        request_delay: float = 0.0,
        page_repository: PageRepository | None = None,
        crawl_repository: CrawlRepository | None = None,
        commit_checkpoints: bool = False,
        page_observer: PageObserver | None = None,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        if max_crawl_time is not None and max_crawl_time <= 0:
            raise ValueError("max_crawl_time must be > 0 (or None for no limit)")
        if request_delay < 0:
            raise ValueError("request_delay must be >= 0")
        self._client = client
        self._evaluator = evaluator
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.max_crawl_time = max_crawl_time
        self.request_delay = request_delay
        self._limiter = RequestRateLimiter(request_delay)
        self._page_repository = page_repository
        self._crawl_repository = crawl_repository
        self._commit_checkpoints = commit_checkpoints  # commit after every page (durable across a hard kill)
        self._page_observer = page_observer  # Phase 8.5: only used while persisting (needs a page_repository)
        self._active_crawl_id: int | None = None
        self._lifecycle = CrawlLifecycle.CREATED
        self._cancel_requested = False
        self._cancel_event: asyncio.Event | None = None  # exists while crawl() runs

    @property
    def lifecycle(self) -> CrawlLifecycle:
        """CREATED -> RUNNING -> (STOPPING) -> COMPLETED | STOPPED | FAILED (see app/crawler/lifecycle.py)."""
        return self._lifecycle

    def cancel(self) -> None:
        """Ask the running crawl to stop gracefully. Safe to call repeatedly, from any task of the loop.

        No new request is started; the page being fetched is finished and persisted, then `crawl()` returns
        a result with ``stop_reason == CANCELLED``. A call before `crawl()` starts applies to the next crawl.
        """
        self._cancel_requested = True
        if self._lifecycle is CrawlLifecycle.RUNNING:
            self._lifecycle = CrawlLifecycle.STOPPING
        if self._cancel_event is not None:
            self._cancel_event.set()

    async def _acquire_slot(self, deadline: float | None) -> str:
        """Rate-limit/deadline gate that cancel() can interrupt: 'ok', 'deadline' or 'cancelled'."""
        if self._cancel_requested:
            return "cancelled"
        if self.request_delay == 0 or self._cancel_event is None:
            return "ok" if await self._limiter.acquire(deadline) else "deadline"
        acquire = asyncio.ensure_future(self._limiter.acquire(deadline))
        cancelled = asyncio.ensure_future(self._cancel_event.wait())
        try:
            await asyncio.wait({acquire, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:  # also on task cancellation: leave no helper task behind
            for task in (acquire, cancelled):
                if not task.done():
                    task.cancel()
            await asyncio.gather(acquire, cancelled, return_exceptions=True)
        if self._cancel_requested:
            return "cancelled"
        return "ok" if acquire.result() else "deadline"

    async def __aenter__(self) -> BfsCrawler:
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the HTTP client (the crawler owns it when used as a context manager)."""
        await self._client.aclose()

    async def crawl(self, seed_url: str, *, crawl_id: int | None = None) -> CrawlResult:
        if self._page_repository is not None and crawl_id is None:
            raise ValueError("crawl_id is required when a page_repository is configured")
        persist = self._page_repository is not None and crawl_id is not None
        self._active_crawl_id = crawl_id if persist else None
        finished = self._finished_status(crawl_id) if persist and crawl_id is not None else None
        if finished in (CrawlStatus.FAILED, CrawlStatus.CANCELLED):  # never restart a terminal crawl
            raise CrawlTerminalError(f"crawl {crawl_id} is {finished.value} and cannot be restarted")
        frozen = finished is CrawlStatus.COMPLETED  # nothing left to do: load state, change nothing
        result = CrawlResult(seed_url, self.max_pages, self.max_depth,
                             max_crawl_time=self.max_crawl_time, request_delay=self.request_delay)
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self.max_crawl_time if self.max_crawl_time is not None else None
        self._limiter.reset()
        self._lifecycle = CrawlLifecycle.STOPPING if self._cancel_requested else CrawlLifecycle.RUNNING
        self._cancel_event = asyncio.Event()
        if self._cancel_requested:
            self._cancel_event.set()
        try:
            return await self._crawl(seed_url, crawl_id, persist, frozen, result, loop, started, deadline)
        finally:
            self._cancel_requested = False  # a cancel applies to one crawl() call
            self._cancel_event = None
            self._active_crawl_id = None

    async def _crawl(
        self, seed_url: str, crawl_id: int | None, persist: bool, frozen: bool, result: CrawlResult,
        loop: asyncio.AbstractEventLoop, started: float, deadline: float | None,
    ) -> CrawlResult:
        gate = UrlGate(self._evaluator)  # fresh per-crawl memory
        decision = gate.admit(seed_url)
        if not decision.allowed or decision.url is None:
            result.stop_reason = StopReason.SEED_REJECTED
            result.seed_rejection = str(decision.reason)
            result.rejections.update({str(k): v for k, v in gate.rejections.items()})
            logger.warning("crawl_seed_rejected", extra={"seed": seed_url, "reason": result.seed_rejection})
            if persist and not frozen and crawl_id is not None:
                self._start_crawl(crawl_id)
                self._finish_crawl(crawl_id, result)
            self._lifecycle = CrawlLifecycle.FAILED
            return result

        result.seed = decision.url
        done: set[str] = set()  # normalised URLs already fetched, incl. redirect targets
        level: list[tuple[str, str | None]] = [(decision.url, None)]
        depth = 0
        later: dict[int, list[tuple[str, str | None]]] = {}  # frontier levels restored from storage
        attempted = 0  # URLs requested by earlier runs of this crawl (count towards max_pages)
        try:
            resumed = self._resume(crawl_id, gate, result, done, frozen=frozen) if persist and crawl_id is not None else None
            if resumed is None:
                result.queued.append(decision.url)
                if persist and not frozen:
                    self._record_discovered(decision.url, 0, None)
                if frozen:
                    level = []
            else:
                level, depth, later, attempted = resumed.level, resumed.start_depth, resumed.later, resumed.attempted
                if frozen:
                    level, later = [], {}
            await self._run_levels(result, gate, done, level, depth, later, attempted, deadline, loop, crawl_id)
        except CrawlPersistenceError as exc:
            exc.result = result  # what had been crawled before the failing write
            self._lifecycle = CrawlLifecycle.FAILED  # the crawl row is left as it was (resumable): the store is unreliable
            raise
        except asyncio.CancelledError:
            # The task was cancelled (e.g. Ctrl-C / task.cancel()): the in-flight URL stays pending. Record the
            # state without awaiting, then let the cancellation propagate as asyncio expects.
            result.stop_reason = StopReason.CANCELLED
            try:
                self._conclude(result, gate, loop, started, persist and not frozen, crawl_id)
            except CrawlPersistenceError:
                logger.error("crawl_state_not_saved_on_cancel", extra={"crawl_id": crawl_id})
            raise
        except Exception as exc:  # noqa: BLE001 - recorded as FAILED, then re-raised (never swallowed)
            self._lifecycle = CrawlLifecycle.FAILED
            logger.error("crawl_failed", extra={"seed": result.seed, "error": repr(exc)})
            if persist and not frozen and crawl_id is not None:
                try:
                    self._fail_crawl(crawl_id, exc)
                except CrawlPersistenceError:
                    pass  # already logged; the original error is what the caller needs
            raise
        if result.stop_reason is StopReason.COMPLETED and result.skipped_by_max_pages and not frozen:
            result.stop_reason = StopReason.MAX_PAGES
        self._conclude(result, gate, loop, started, persist and not frozen, crawl_id)
        logger.info(
            "crawl_finished",
            extra={"seed": result.seed, "visited": len(result.pages), "fetched": len(result.fetched),
                   "failed": len(result.failed), "stop": result.stop_reason.value,
                   "depth": result.deepest_level, "elapsed": round(result.elapsed, 3)},
        )
        return result

    def _conclude(
        self, result: CrawlResult, gate: UrlGate, loop: asyncio.AbstractEventLoop, started: float,
        persist: bool, crawl_id: int | None,
    ) -> None:
        """Common end of a run (completed, limited, cancelled): final numbers, lifecycle, crawl row."""
        result.rejections.update({str(k): v for k, v in gate.rejections.items()})
        result.elapsed = loop.time() - started
        result.time_limited = result.stop_reason is StopReason.MAX_CRAWL_TIME
        self._lifecycle = (
            CrawlLifecycle.COMPLETED if result.stop_reason is StopReason.COMPLETED else CrawlLifecycle.STOPPED
        )
        if persist and crawl_id is not None:
            self._finish_crawl(crawl_id, result)

    async def _run_levels(
        self, result: CrawlResult, gate: UrlGate, done: set[str], level: list[tuple[str, str | None]],
        depth: int, later: dict[int, list[tuple[str, str | None]]], attempted: int,
        deadline: float | None, loop: asyncio.AbstractEventLoop, crawl_id: int | None,
    ) -> None:
        while level and result.stop_reason is StopReason.COMPLETED:
            result.current_depth = depth
            next_level: list[tuple[str, str | None]] = later.pop(depth + 1, [])
            for url, parent in level:
                if self._cancel_requested:  # nothing new is started once cancel() was called
                    result.stop_reason = StopReason.CANCELLED
                    break
                if attempted + len(result.pages) >= self.max_pages:
                    result.stop_reason = StopReason.MAX_PAGES
                    break
                if url in done:  # queued earlier, then fetched as another URL's redirect target
                    result.redirect_duplicates.append(url)
                    continue
                # Time limit and rate limit are applied before the request is started.
                slot = await self._acquire_slot(deadline)  # a cancel() during the delay ends the wait
                if slot == "cancelled":
                    result.stop_reason = StopReason.CANCELLED
                    break
                if slot == "deadline":
                    result.stop_reason = StopReason.MAX_CRAWL_TIME
                    break
                # The wait may have ended at (or, with a late wake-up, after) the deadline: check
                # again so no request is started once the budget is gone.
                if deadline is not None and loop.time() >= deadline:
                    result.stop_reason = StopReason.MAX_CRAWL_TIME
                    break
                try:
                    async with asyncio.timeout_at(deadline) as budget:  # deadline None = no limit
                        page = await self._visit(url, parent, depth, gate, done, result, next_level)
                except TimeoutError:
                    if not budget.expired():  # pragma: no cover - not ours, never swallow it
                        raise
                    # The budget ran out mid-request: the in-flight fetch was cancelled and
                    # is discarded (its URL stays pending); nothing else is started.
                    result.stop_reason = StopReason.MAX_CRAWL_TIME
                    break
                result.pages.append(page)
                if self._page_repository is not None and crawl_id is not None:
                    self._persist_page(self._page_repository, crawl_id, page, attempted + len(result.pages))
                result.deepest_level = depth
            level = next_level
            depth += 1
            if not level and later:  # stored frontier with a gap in depth: continue with the next one
                depth = min(later)
                level = later.pop(depth)

    async def _visit(
        self, url: str, parent: str | None, depth: int, gate: UrlGate,
        done: set[str], result: CrawlResult, next_level: list[tuple[str, str | None]],
    ) -> CrawledPage:
        fetched = await self._client.fetch(url)
        done.add(url)
        self._mark_final_url_seen(url, fetched, gate, done)
        if not fetched.ok or not self._is_html(fetched):
            return CrawledPage(url, depth, parent, fetched)
        parsed = self._parse(url, fetched)
        try:
            extraction = extract_links(fetched.text, fetched.final_url)
        except Exception as exc:  # noqa: BLE001 - a parser bug must not stop the crawl
            logger.warning("link_extraction_failed", extra={"url": url, "error": repr(exc)})
            return CrawledPage(url, depth, parent, fetched, parsed=parsed)
        found = len(extraction.hrefs)
        queued = 0
        if depth >= self.max_depth:
            if found:
                result.depth_limited = True
                result.skipped_by_depth += found
            return CrawledPage(url, depth, parent, fetched, found, 0, parsed)
        for href in extraction.hrefs:
            decision = gate.admit(href, base=extraction.base_url)
            if not decision.allowed or decision.url is None:
                continue
            if len(result.queued) >= self.max_pages:
                result.skipped_by_max_pages += 1
                self._record_discovered(decision.url, depth + 1, url, skipped=True)
                continue
            result.queued.append(decision.url)
            self._record_discovered(decision.url, depth + 1, url)
            next_level.append((decision.url, url))
            queued += 1
        return CrawledPage(url, depth, parent, fetched, found, queued, parsed)

    @staticmethod
    def _parse(url: str, fetched: PageResult) -> ParsedPage | None:
        """Phase 7.1 content parsing. Never raises: a failure is logged and the page simply has no `parsed`."""
        try:
            return parse_response(fetched)
        except Exception as exc:  # noqa: BLE001 - a parser bug must not stop the crawl
            logger.warning("html_parse_failed", extra={"url": url, "error": repr(exc)})
            return None

    @contextmanager
    def _persistence_guard(self, what: str, crawl_id: int, url: str | None = None) -> Iterator[None]:
        """Run one persistence step atomically (SAVEPOINT): on a database error the step is rolled
        back, so no partial or false visited state remains, and a CrawlPersistenceError is raised."""
        assert self._page_repository is not None
        try:
            with self._page_repository.session.begin_nested():
                yield
        except SQLAlchemyError as exc:
            logger.error(
                "crawl_persistence_failed",
                extra={"action": what, "crawl_id": crawl_id, "url": url, "error": repr(exc)},
            )
            where = f" ({url})" if url else ""
            raise CrawlPersistenceError(
                f"could not persist {what} for crawl {crawl_id}{where}: {type(exc).__name__}"
            ) from exc

    def _commit(self, crawl_id: int) -> None:
        if self._commit_checkpoints and self._page_repository is not None:
            try:
                self._page_repository.session.commit()
            except SQLAlchemyError as exc:
                logger.error("crawl_persistence_failed",
                             extra={"action": "commit", "crawl_id": crawl_id, "error": repr(exc)})
                raise CrawlPersistenceError(f"could not commit crawl {crawl_id}: {type(exc).__name__}") from exc

    def _resume(
        self, crawl_id: int, gate: UrlGate, result: CrawlResult, done: set[str], *, frozen: bool = False
    ):  # -> ResumeState | None
        """Mark the crawl running and reload stored state (visited set, queue, UrlGate memory).

        `frozen` (the crawl is already COMPLETED) only reads: no status change, no promotion of skipped rows."""
        repo = self._page_repository
        assert repo is not None
        if not frozen:
            self._start_crawl(crawl_id)
        with self._persistence_guard("resume state", crawl_id):
            state = load_resume_state(
                repo, crawl_id, max_pages=self.max_pages, normalize=self._normalize_or_raw, promote=not frozen
            )
        if state is None:
            return None
        rejections = Counter(gate.rejections)
        for known in state.known:  # rebuilds gate.seen and the trap counters with the normal rules
            gate.admit(known)
            gate.seen.add(known)
        gate.rejections.clear()
        gate.rejections.update(rejections)  # replaying stored URLs is not a rejection
        done.update(state.done)
        for final in state.done:
            gate.seen.add(final)
        result.queued.extend(state.queued)
        result.previously_visited = list(state.visited)
        result.deepest_level = state.deepest_level
        result.skipped_by_max_pages += state.skipped_left
        logger.info("crawl_resumed", extra={"crawl_id": crawl_id, "visited": state.attempted,
                                            "queued": len(state.queued), "skipped": state.skipped_left})
        return state

    def _normalize_or_raw(self, url: str) -> str:
        try:
            return self._evaluator.normalize(url).url
        except UrlRejected:
            return url

    def _record_discovered(self, url: str, depth: int, parent: str | None, *, skipped: bool = False) -> None:
        """Store a queued URL as PENDING (or SKIPPED when max_pages kept it out of the queue)."""
        repo, crawl_id = self._page_repository, self._active_crawl_id
        if repo is None or crawl_id is None:
            return
        status = PageStatus.SKIPPED if skipped else PageStatus.PENDING
        with self._persistence_guard("discovered url", crawl_id, url):
            repo.get_or_create(crawl_id, url, depth=depth, status=status, parent_url=parent)

    def _start_crawl(self, crawl_id: int) -> None:
        repo = self._crawl_repository
        if repo is None:
            return
        with self._persistence_guard("crawl start", crawl_id):
            crawl = repo.require(crawl_id)
            repo.update(crawl, max_pages=self.max_pages, max_depth=self.max_depth,
                        max_crawl_time=self.max_crawl_time, request_delay=self.request_delay,
                        stop_reason=None)  # a running crawl has no stop reason yet
            if crawl.status is CrawlStatus.PENDING:
                repo.set_status(crawl, CrawlStatus.RUNNING)
        self._commit(crawl_id)

    def _finished_status(self, crawl_id: int) -> CrawlStatus | None:
        """The crawl's terminal status (completed / failed / cancelled), or None if it can still run."""
        repo = self._crawl_repository
        if repo is None:
            return None
        status = repo.require(crawl_id).status
        return status if status in (CrawlStatus.COMPLETED, CrawlStatus.FAILED, CrawlStatus.CANCELLED) else None

    def _fail_crawl(self, crawl_id: int, exc: Exception) -> None:
        """Fatal crawler error: record FAILED (with the reason) so the crawl is never silently resumed."""
        repo = self._crawl_repository
        if repo is None:
            self._commit(crawl_id)
            return
        with self._persistence_guard("crawl failure", crawl_id):
            crawl = repo.require(crawl_id)
            if crawl.status in (CrawlStatus.PENDING, CrawlStatus.RUNNING):
                repo.set_status(crawl, CrawlStatus.FAILED, error_message=f"{type(exc).__name__}: {exc}")
        self._commit(crawl_id)

    def _finish_crawl(self, crawl_id: int, result: CrawlResult) -> None:
        """Record why this run stopped. A crawl stopped by a limit stays RUNNING so it can be continued."""
        repo = self._crawl_repository
        if repo is None:
            self._commit(crawl_id)
            return
        with self._persistence_guard("crawl finish", crawl_id):
            crawl = repo.require(crawl_id)
            repo.update(crawl, elapsed_seconds=crawl.elapsed_seconds + result.elapsed,
                        stop_reason=CrawlStopReason(result.stop_reason.value),
                        current_depth=result.current_depth, last_checkpoint_at=utcnow())
            if crawl.status in (CrawlStatus.PENDING, CrawlStatus.RUNNING):
                if result.stop_reason is StopReason.COMPLETED:
                    repo.set_status(crawl, CrawlStatus.COMPLETED)
                elif result.stop_reason is StopReason.SEED_REJECTED:
                    repo.set_status(crawl, CrawlStatus.FAILED, error_message=result.seed_rejection)
        self._commit(crawl_id)

    def _persist_page(self, repo: PageRepository, crawl_id: int, page: CrawledPage, attempted: int) -> None:
        """Store one completed fetch (page row + crawl progress) atomically, then optionally commit."""
        with self._persistence_guard("page", crawl_id, page.url):
            self._store_page(repo, crawl_id, page)
            self._observe(repo, crawl_id, page)
            if self._crawl_repository is not None:
                crawl = self._crawl_repository.require(crawl_id)
                self._crawl_repository.update(
                    crawl, pages_crawled=attempted, current_depth=page.depth, last_checkpoint_at=utcnow()
                )
        self._commit(crawl_id)

    def _observe(self, repo: PageRepository, crawl_id: int, page: CrawledPage) -> None:
        """Phase 8.5: run the page observer in its own SAVEPOINT inside the page's.

        A database error propagates (the persistence guard then rolls the page back too, so the page is requested
        again on resume and nothing is lost). Any other error is logged and only the observer's own writes are
        rolled back: a bug in a downstream consumer must not stop or fail the crawl."""
        if self._page_observer is None:
            return
        try:
            with repo.session.begin_nested():
                self._page_observer(page, crawl_id)
        except SQLAlchemyError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let an observer stop the crawl
            logger.warning("page_observer_failed", extra={"url": page.url, "crawl_id": crawl_id, "error": repr(exc)})

    def _store_page(self, repo: PageRepository, crawl_id: int, page: CrawledPage) -> None:
        fetched = page.result
        row, _ = repo.get_or_create(crawl_id, page.url, depth=page.depth)
        try:
            final_normalized = self._evaluator.normalize(fetched.final_url).url
        except UrlRejected:
            final_normalized = fetched.final_url
        meta = {
            "final_url": fetched.final_url,
            "charset": fetched.charset,
            "response_size": fetched.size,
            "duration_seconds": fetched.duration,
            "redirect_chain": [
                {"url": h.url, "status_code": h.status_code, "location": h.location} for h in fetched.redirects
            ],
            "outcome": PageOutcome(fetched.outcome.value),
            "dedupe_key": Page.hash_url(final_normalized),
        }
        if fetched.ok and fetched.status_code is not None:
            title = page.parsed.title if page.parsed is not None else None
            repo.mark_fetched(
                row, http_status=fetched.status_code, content_type=fetched.content_type, title=title, **meta
            )
        else:
            message = fetched.error.message if fetched.error is not None else fetched.outcome.value
            repo.mark_failed(row, message, http_status=fetched.status_code, **meta)

    def _mark_final_url_seen(
        self, url: str, fetched: PageResult, gate: UrlGate, done: set[str]
    ) -> None:
        """A redirect target counts as visited, so it is not fetched again via another link."""
        if fetched.final_url == url:
            return
        try:
            n = self._evaluator.normalize(fetched.final_url)
            gate.seen.add(n.url)
            done.add(n.url)
        except UrlRejected:
            pass

    @staticmethod
    def _is_html(fetched: PageResult) -> bool:
        return fetched.content_type is None or fetched.content_type in HTML_MEDIA_TYPES


def build_crawler(
    settings: Settings,
    seed_url: str,
    *,
    resolver: Resolver | None = None,
    policy: UrlPolicy | None = None,
    client_config: HttpClientConfig | None = None,
) -> BfsCrawler:
    """Create a crawler for `seed_url` from `APP_*` settings, sharing one `UrlEvaluator` with
    the HTTP client. Use `async with build_crawler(...) as crawler:` to close the client."""
    evaluator = UrlEvaluator(policy or UrlPolicy.from_settings(settings), seed_url=seed_url)
    kwargs = {"resolver": resolver} if resolver is not None else {}
    client = AsyncHttpClient(
        client_config or HttpClientConfig.from_settings(settings), evaluator=evaluator, **kwargs
    )
    return BfsCrawler(
        client, evaluator,
        max_pages=settings.crawler_max_pages, max_depth=settings.crawler_max_depth,
        max_crawl_time=settings.crawler_max_crawl_time or None,  # 0 = no limit
        request_delay=settings.crawler_request_delay,
    )
