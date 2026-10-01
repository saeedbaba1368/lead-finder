"""Crawl lifecycle model (Phase 6.4).

`CrawlLifecycle` is the vocabulary used by `BfsCrawler.lifecycle` and the state report. It maps onto the
persisted `Crawl.status` + `Crawl.stop_reason`; no extra column is needed:

=========  ==============================================================================
CREATED    status ``pending``
RUNNING    status ``running`` with no stop reason (a run is active, or the process was killed mid-run)
STOPPING   in memory only: cancellation was requested and the current operation is finishing
COMPLETED  status ``completed`` (frontier exhausted)
STOPPED    status ``running`` + stop reason ``cancelled`` / ``max_pages`` / ``max_crawl_time``
           (resumable: the stored visited set and frontier are kept), or status ``cancelled``
FAILED     status ``failed`` (fatal crawler error or rejected seed)
=========  ==============================================================================
"""

from __future__ import annotations

from enum import StrEnum

from app.models import CrawlStatus, CrawlStopReason


class CrawlLifecycle(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class CrawlTerminalError(RuntimeError):
    """The crawl is already in a terminal state (failed / cancelled) and is never restarted."""


def lifecycle_of(status: CrawlStatus, stop_reason: CrawlStopReason | None) -> CrawlLifecycle:
    """Lifecycle of a persisted crawl."""
    if status is CrawlStatus.PENDING:
        return CrawlLifecycle.CREATED
    if status is CrawlStatus.COMPLETED:
        return CrawlLifecycle.COMPLETED
    if status is CrawlStatus.FAILED:
        return CrawlLifecycle.FAILED
    if status is CrawlStatus.CANCELLED or stop_reason is not None:
        return CrawlLifecycle.STOPPED
    return CrawlLifecycle.RUNNING


def is_resumable(status: CrawlStatus) -> bool:
    """Only crawls that are not in a terminal status can be continued."""
    return status in (CrawlStatus.PENDING, CrawlStatus.RUNNING)
