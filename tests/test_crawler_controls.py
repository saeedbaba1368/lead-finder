"""Crawl controls (Phase 6.3.1): max_crawl_time, request_delay, async rate limiting, and their
interaction with max_pages / max_depth. Loopback servers only; no internet."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.config import Settings
from app.crawler import AsyncHttpClient, BfsCrawler, StopReason, build_crawler
from app.crawler.ratelimit import RequestRateLimiter
from app.urls import UrlEvaluator, UrlPolicy
from tests.test_crawler_bfs import local_network, paths  # noqa: F401  (fixture re-export)

HTML = {"Content-Type": "text/html; charset=utf-8"}


class Timed:
    """Records (path, monotonic time) of every request the server receives."""

    def __init__(self) -> None:
        self.log: list[tuple[str, float]] = []

    @property
    def requests(self) -> list[str]:
        return [p for p, _ in self.log]

    @property
    def times(self) -> list[float]:
        return [t for _, t in self.log]


def _page(*hrefs: str) -> bytes:
    return ("<html><body>" + "".join(f'<a href="{h}">l</a>' for h in hrefs) + "</body></html>").encode()


@contextmanager
def timed_site(routes: dict[str, tuple[bytes, float]]):
    """`routes`: path -> (html body, seconds the server takes to answer)."""
    state = Timed()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            state.log.append((self.path, time.monotonic()))
            body, pause = routes.get(self.path, (b"nope", 0.0))
            if pause:
                time.sleep(pause)
            self.send_response(200 if self.path in routes else 404)
            for k, v in HTML.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def chain(n: int, pause: float = 0.0) -> dict[str, tuple[bytes, float]]:
    """/ -> /p1 -> /p2 -> ... -> /p{n-1}: one new page per depth level."""
    routes = {"/": (_page("/p1" if n > 1 else "/"), pause)}
    for i in range(1, n):
        routes[f"/p{i}"] = (_page(f"/p{i + 1}") if i + 1 < n else _page(), pause)
    return routes


def fan(n: int, pause: float = 0.0) -> dict[str, tuple[bytes, float]]:
    """/ -> /p1 ... /p{n-1} (all at depth 1, no further links)."""
    routes = {"/": (_page(*[f"/p{i}" for i in range(1, n)]), pause)}
    for i in range(1, n):
        routes[f"/p{i}"] = (_page(), pause)
    return routes


def make_crawler(base: str, **kwargs) -> BfsCrawler:
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy, seed_url=base)
    client = AsyncHttpClient(evaluator=evaluator, resolver=lambda host: ["127.0.0.1"])
    return BfsCrawler(client, evaluator, **kwargs)


def run(base: str, **kwargs):
    async def main():
        async with make_crawler(base, **kwargs) as crawler:
            result = await crawler.crawl(base + "/")
            leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return result, leftovers

    return asyncio.run(main())


# --- RequestRateLimiter (no network) ---------------------------------------------------------

def test_limiter_rejects_negative_delay():
    with pytest.raises(ValueError):
        RequestRateLimiter(-0.1)


def test_limiter_first_request_is_immediate_and_zero_delay_never_waits():
    async def main():
        t0 = time.monotonic()
        limiter = RequestRateLimiter(0)
        for _ in range(50):
            assert await limiter.acquire()
        first = RequestRateLimiter(5)
        assert await first.acquire()  # first request is not delayed
        return time.monotonic() - t0

    assert asyncio.run(main()) < 0.5


def test_limiter_spaces_concurrent_callers_no_burst():
    async def main():
        limiter = RequestRateLimiter(0.1)
        stamps: list[float] = []

        async def worker():
            assert await limiter.acquire()
            stamps.append(time.monotonic())

        await asyncio.gather(*(worker() for _ in range(5)))
        return sorted(stamps)

    stamps = asyncio.run(main())
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 4 and all(g >= 0.08 for g in gaps), gaps  # 5 callers released 0.1s apart


def test_limiter_does_not_block_the_event_loop():
    async def main():
        limiter = RequestRateLimiter(0.3)
        await limiter.acquire()
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        await limiter.acquire()  # waits ~0.3s
        task.cancel()
        return ticks

    assert asyncio.run(main()) >= 10  # the loop kept running while the limiter waited


def test_limiter_refuses_a_slot_past_the_deadline_without_sleeping():
    async def main():
        loop = asyncio.get_running_loop()
        limiter = RequestRateLimiter(5)
        assert await limiter.acquire(loop.time() + 1)  # slot 0 is fine
        t0 = time.monotonic()
        assert not await limiter.acquire(loop.time() + 1)  # next slot is 5s away: refused at once
        assert not await RequestRateLimiter(0).acquire(loop.time() - 1)  # already expired
        return time.monotonic() - t0

    assert asyncio.run(main()) < 0.2


def test_limiter_reset_forgets_previous_requests():
    async def main():
        limiter = RequestRateLimiter(10)
        await limiter.acquire()
        limiter.reset()
        t0 = time.monotonic()
        assert await limiter.acquire()
        return time.monotonic() - t0

    assert asyncio.run(main()) < 0.2


# --- validation and configuration ------------------------------------------------------------

def test_invalid_control_values_are_rejected():
    with pytest.raises(ValueError):
        make_crawler("http://127.0.0.1:1", max_crawl_time=0)
    with pytest.raises(ValueError):
        make_crawler("http://127.0.0.1:1", max_crawl_time=-1)
    with pytest.raises(ValueError):
        make_crawler("http://127.0.0.1:1", request_delay=-0.5)


def test_defaults_keep_6_2_behaviour(local_network):
    with timed_site(fan(4)) as (base, state):
        result, _ = run(base, max_depth=5)
    assert result.stop_reason is StopReason.COMPLETED and not result.time_limited
    assert result.max_crawl_time is None and result.request_delay == 0.0
    assert state.requests == ["/", "/p1", "/p2", "/p3"]


def test_settings_defaults_validation_and_build_crawler(local_network):
    from pydantic import ValidationError

    s = Settings(_env_file=None)
    assert s.crawler_max_crawl_time == 0 and s.crawler_request_delay == 0
    for bad in ({"crawler_max_crawl_time": -1}, {"crawler_request_delay": -0.1},
                {"crawler_max_crawl_time": 86_401}, {"crawler_request_delay": 3_601}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **bad)

    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    with timed_site(fan(3)) as (base, _):
        async def main():
            async with build_crawler(Settings(_env_file=None), base, policy=policy,
                                     resolver=lambda h: ["127.0.0.1"]) as c:
                assert c.max_crawl_time is None and c.request_delay == 0  # 0 means "no limit"
            cfg = Settings(_env_file=None, crawler_max_crawl_time=12.5, crawler_request_delay=0.25)
            async with build_crawler(cfg, base, policy=policy, resolver=lambda h: ["127.0.0.1"]) as c:
                assert (c.max_crawl_time, c.request_delay) == (12.5, 0.25)

        asyncio.run(main())


# --- maximum crawl time ----------------------------------------------------------------------

def test_max_crawl_time_stops_cleanly_between_requests(local_network):
    with timed_site(chain(20, pause=0.2)) as (base, state):
        result, leftovers = run(base, max_depth=50, max_crawl_time=0.5)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME and result.time_limited
    assert 1 <= len(result.pages) < 20
    assert result.elapsed < 1.2
    assert result.pending  # work was left undone
    assert leftovers == []


def test_no_request_is_started_after_the_limit(local_network):
    with timed_site(chain(20, pause=0.2)) as (base, state):
        result, _ = run(base, max_depth=50, max_crawl_time=0.5)
        seen = list(state.requests)
        time.sleep(0.6)  # nothing may still be running in the background
        assert state.requests == seen
    started_late = [t for t in state.times if t - state.times[0] > 0.5 + 0.1]
    assert started_late == []
    assert len(seen) <= 4  # 0.2s each, 0.5s budget: at most the 3rd/4th request in flight


def test_in_flight_request_is_bounded_by_the_budget(local_network):
    with timed_site({"/": (_page("/a"), 0.0), "/a": (_page(), 1.5)}) as (base, state):
        result, leftovers = run(base, max_crawl_time=0.3)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME
    assert result.elapsed < 1.0  # did not wait for the 1.5s response
    assert paths(result.visited) == ["/"]  # the abandoned fetch is not reported as a page
    assert paths(result.pending) == ["/a"]
    assert leftovers == []


def test_limit_already_exhausted_fetches_nothing(local_network):
    with timed_site(fan(3)) as (base, state):
        result, leftovers = run(base, max_crawl_time=1e-9)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME and result.pages == []
    assert leftovers == []


def test_generous_limit_does_not_change_the_crawl(local_network):
    with timed_site(fan(4)) as (base, state):
        result, _ = run(base, max_depth=5, max_crawl_time=30)
    assert result.stop_reason is StopReason.COMPLETED and not result.time_limited
    assert state.requests == ["/", "/p1", "/p2", "/p3"] and result.elapsed < 5


def test_crawl_finishing_exactly_inside_budget_is_completed_not_time_limited(local_network):
    with timed_site(fan(2)) as (base, _):
        result, _ = run(base, max_crawl_time=5)
    assert result.stop_reason is StopReason.COMPLETED and result.pending == []


# --- request delay / async rate limiting -----------------------------------------------------

def test_request_delay_spaces_requests(local_network):
    with timed_site(fan(4)) as (base, state):
        result, _ = run(base, max_depth=5, request_delay=0.25)
    assert state.requests == ["/", "/p1", "/p2", "/p3"]
    gaps = [b - a for a, b in zip(state.times, state.times[1:])]
    assert all(g >= 0.2 for g in gaps), gaps  # server-side start gaps honour the delay
    assert result.elapsed >= 0.7 and result.stop_reason is StopReason.COMPLETED


def test_first_request_is_not_delayed(local_network):
    with timed_site(fan(1)) as (base, state):
        result, _ = run(base, request_delay=3)
    assert state.requests == ["/"] and result.elapsed < 1.0


def test_rate_limiting_does_not_block_the_event_loop(local_network):
    with timed_site(fan(4)) as (base, state):
        async def main():
            ticks = 0

            async def ticker():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.01)
                    ticks += 1

            task = asyncio.create_task(ticker())
            async with make_crawler(base, max_depth=5, request_delay=0.2) as crawler:
                result = await crawler.crawl(base + "/")
            task.cancel()
            return result, ticks

        result, ticks = asyncio.run(main())
    assert len(result.pages) == 4
    assert ticks >= 30  # ~0.6s of waiting at 10ms ticks; a blocking sleep would give ~0


def test_two_crawlers_each_throttle_independently_on_one_loop(local_network):
    with timed_site(fan(3)) as (base, state):
        async def main():
            async with make_crawler(base, request_delay=0.2) as a, make_crawler(base, request_delay=0.2) as b:
                return await asyncio.gather(a.crawl(base + "/"), b.crawl(base + "/"))

        ra, rb = asyncio.run(main())
    assert len(ra.pages) == len(rb.pages) == 3
    assert ra.elapsed >= 0.35 and rb.elapsed >= 0.35


def test_delay_restarts_for_each_crawl(local_network):
    with timed_site(fan(2)) as (base, state):
        async def main():
            async with make_crawler(base, request_delay=0.3) as crawler:
                first = await crawler.crawl(base + "/")
                t0 = time.monotonic()
                second = await crawler.crawl(base + "/")
                return first, second

        first, second = asyncio.run(main())
    assert len(first.pages) == len(second.pages) == 2


# --- interaction with max_pages / max_depth --------------------------------------------------

def test_max_pages_reached_before_time_limit(local_network):
    with timed_site(fan(10)) as (base, state):
        result, _ = run(base, max_pages=3, max_crawl_time=30)
    assert result.stop_reason is StopReason.MAX_PAGES and not result.time_limited
    assert len(state.requests) == 3


def test_time_limit_reached_before_max_pages(local_network):
    with timed_site(fan(30, pause=0.15)) as (base, state):
        result, leftovers = run(base, max_pages=25, max_crawl_time=0.5)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME
    assert 1 <= len(result.pages) < 25 and len(state.requests) < 10
    assert leftovers == []


def test_max_pages_and_time_with_delay_stop_on_whichever_comes_first(local_network):
    with timed_site(fan(10)) as (base, state):
        result, _ = run(base, max_pages=2, max_crawl_time=30, request_delay=0.05)
    assert result.stop_reason is StopReason.MAX_PAGES and len(state.requests) == 2


def test_max_depth_reached_before_time_limit(local_network):
    with timed_site(chain(10)) as (base, state):
        result, _ = run(base, max_depth=2, max_crawl_time=30)
    assert result.stop_reason is StopReason.COMPLETED and result.depth_limited
    assert state.requests == ["/", "/p1", "/p2"] and not result.time_limited


def test_time_limit_reached_before_max_depth(local_network):
    with timed_site(chain(30, pause=0.2)) as (base, state):
        result, leftovers = run(base, max_depth=25, max_crawl_time=0.5)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME
    assert result.deepest_level < 25 and len(state.requests) < 6
    assert leftovers == []


def test_delay_plus_time_limit_does_not_sleep_past_the_deadline(local_network):
    # Request 1 at t=0, request 2 at t=0.4; the 3rd slot (t=0.8) is after the 0.5s budget, so the
    # crawler must stop right after request 2 instead of sleeping until 0.8s.
    with timed_site(fan(10)) as (base, state):
        result, leftovers = run(base, max_crawl_time=0.5, request_delay=0.4)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME
    assert len(result.pages) == 2 and len(state.requests) == 2
    assert result.elapsed < 0.7
    assert leftovers == []


def test_delay_longer_than_budget_fetches_only_the_seed(local_network):
    with timed_site(fan(5)) as (base, state):
        result, _ = run(base, max_crawl_time=0.3, request_delay=5)
    assert state.requests == ["/"] and result.stop_reason is StopReason.MAX_CRAWL_TIME
    assert result.elapsed < 0.8


def test_all_four_limits_together(local_network):
    with timed_site(chain(10)) as (base, state):
        result, _ = run(base, max_pages=4, max_depth=6, max_crawl_time=30, request_delay=0.05)
    assert result.stop_reason is StopReason.MAX_PAGES and len(state.requests) == 4


# --- clean shutdown --------------------------------------------------------------------------

def test_clean_shutdown_after_time_limit_and_reusable(local_network):
    with timed_site(chain(20, pause=0.2)) as (base, state):
        async def main():
            async with make_crawler(base, max_depth=50, max_crawl_time=0.5, request_delay=0.05) as crawler:
                first = await crawler.crawl(base + "/")
                second = await crawler.crawl(base + "/")  # fresh clock, fresh limiter
                leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return first, second, leftovers, crawler

        first, second, leftovers, crawler = asyncio.run(main())
    assert first.stop_reason is second.stop_reason is StopReason.MAX_CRAWL_TIME
    assert len(second.pages) >= 1 and leftovers == []
    assert crawler._client._client.is_closed  # the context manager closed the HTTP client


def test_cancelling_a_delayed_crawl_shuts_down_without_hanging(local_network):
    with timed_site(fan(5)) as (base, state):
        async def main():
            async with make_crawler(base, request_delay=30) as crawler:
                task = asyncio.create_task(crawler.crawl(base + "/"))
                await asyncio.sleep(0.3)  # seed done, waiting for the next slot
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        t0 = time.monotonic()
        leftovers = asyncio.run(main())
    assert time.monotonic() - t0 < 3 and leftovers == [] and state.requests == ["/"]


# --- Phase 6.3.2.1 hardening -----------------------------------------------------------------

def test_no_blocking_time_sleep_is_used_by_crawler_or_limiter(local_network, monkeypatch):
    calls: list[float] = []

    def forbidden(seconds):  # would block the whole event loop
        calls.append(seconds)
        raise AssertionError("time.sleep called from crawler code")

    with timed_site(fan(3)) as (base, state):
        monkeypatch.setattr(time, "sleep", forbidden, raising=True)  # server thread not needed to sleep (pause=0)
        result, _ = run(base, max_depth=3, max_crawl_time=10, request_delay=0.05)
    assert calls == [] and len(result.pages) == 3


def test_deadline_uses_the_monotonic_clock_not_the_wall_clock(local_network, monkeypatch):
    # A wall clock that jumps an hour forward must neither stop the crawl nor change `elapsed`.
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    with timed_site(fan(3)) as (base, state):
        result, _ = run(base, max_crawl_time=30, request_delay=0.05)
    assert result.stop_reason is StopReason.COMPLETED and len(result.pages) == 3
    assert 0 <= result.elapsed < 5


def test_slot_waking_after_the_deadline_starts_no_request(local_network):
    # The limiter grants the slot, but the (simulated) wake-up lands after the deadline:
    # the crawler must re-check and not start the request.
    class LateLimiter(RequestRateLimiter):
        async def acquire(self, deadline=None):
            ok = await super().acquire(deadline)
            if ok and self.calls:
                await asyncio.sleep(0.6)  # oversleep past a 0.4s budget
            self.calls += 1
            return ok

    with timed_site(fan(4)) as (base, state):
        async def main():
            async with make_crawler(base, max_crawl_time=0.4, request_delay=0.01) as crawler:
                limiter = LateLimiter(0.01)
                limiter.calls = 0
                crawler._limiter = limiter
                fetched: list[str] = []
                real_fetch = crawler._client.fetch

                async def spy(url, *a, **kw):
                    fetched.append(url)
                    return await real_fetch(url, *a, **kw)

                crawler._client.fetch = spy
                result = await crawler.crawl(base + "/")
                return result, fetched

        result, fetched = asyncio.run(main())
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME
    assert len(fetched) == 1  # the client was not even entered for the late slot
    assert state.requests == ["/"] and len(result.pages) == 1
    assert len(result.pending) >= 1  # discovered but never requested


def test_state_is_consistent_after_a_time_limited_crawl(local_network):
    with timed_site(chain(20, pause=0.15)) as (base, state):
        result, leftovers = run(base, max_depth=50, max_crawl_time=0.5)
    assert result.stop_reason is StopReason.MAX_CRAWL_TIME and result.time_limited
    assert set(result.visited) <= set(result.queued)
    assert len(result.visited) == len(set(result.visited))  # no page recorded twice
    assert set(result.pending).isdisjoint(result.visited)
    assert leftovers == []
