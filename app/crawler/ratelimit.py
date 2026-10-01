"""Asynchronous request-start rate limiter (Phase 6.3.1). No I/O, no dependencies.

`RequestRateLimiter(delay)` guarantees that request *starts* are at least `delay` seconds
apart. Slots are reserved under a lock and the wait happens outside it with `asyncio.sleep`,
so the event loop is never blocked and several concurrent callers are spaced out (never
released as a burst). The first request is not delayed.
"""

from __future__ import annotations

import asyncio


class RequestRateLimiter:
    def __init__(self, delay: float = 0.0) -> None:
        if delay < 0:
            raise ValueError("request_delay must be >= 0")
        self.delay = float(delay)
        self._next_slot = 0.0  # loop time of the earliest allowed next start
        self._lock: asyncio.Lock | None = None  # created lazily, inside the running loop

    def reset(self) -> None:
        """Forget previous requests (a new crawl starts without an inherited delay)."""
        self._next_slot = 0.0

    async def acquire(self, deadline: float | None = None) -> bool:
        """Wait for the next request slot. `deadline` is an absolute loop time.

        Returns False, without waiting, if the slot would fall at or after the deadline (the
        caller must not start the request). Returns True once the slot has arrived.
        """
        if self.delay == 0:
            return deadline is None or asyncio.get_running_loop().time() < deadline
        if self._lock is None:
            self._lock = asyncio.Lock()
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            slot = max(now, self._next_slot)
            if deadline is not None and slot >= deadline:
                return False
            self._next_slot = slot + self.delay
        wait = slot - now
        if wait > 0:
            await asyncio.sleep(wait)
        return True
