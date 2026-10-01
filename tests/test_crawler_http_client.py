"""AsyncHttpClient against local loopback servers and httpx.MockTransport (no internet).

The suite-wide network guard (conftest) is lifted only in the `local_network` fixture, and the
tests only ever talk to 127.0.0.1. Async code is driven with `asyncio.run` (no plugin needed).
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.core.config import Settings
from app.crawler import AsyncHttpClient, FetchOutcome, HttpClientConfig
from app.urls import UrlEvaluator, UrlPolicy

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_GETADDRINFO = socket.getaddrinfo

HTML = b"<html><head><title>hi</title></head><body>hello</body></html>"
HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


@pytest.fixture
def local_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket.socket, "connect_ex", _REAL_CONNECT_EX)
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)


class State:
    def __init__(self) -> None:
        self.connections = 0
        self.requests: list[str] = []


def make_handler(routes, state: State):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive, needed for the connection-reuse test

        def setup(self):
            state.connections += 1
            super().setup()

        def do_GET(self):
            state.requests.append(self.path)
            if self.path == "/slow":
                time.sleep(1.5)
            status, body, headers = routes.get(self.path, (404, b"nope", {}))
            headers = dict(headers)
            no_length = headers.pop("X-No-Length", None)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            if no_length:
                self.send_header("Connection", "close")
                self.close_connection = True
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    return Handler


@contextmanager
def server(routes) -> Iterator[tuple[str, State]]:
    state = State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(routes, state))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def run_fetch(urls, config=None, **kwargs):
    async def main():
        async with AsyncHttpClient(config, **kwargs) as client:
            return [await client.fetch(u) for u in urls]

    return asyncio.run(main())


def fetch_one(url, config=None, **kwargs):
    return run_fetch([url], config, **kwargs)[0]


# --- success ---------------------------------------------------------------------------------

def test_successful_get_returns_structured_metadata(local_network):
    routes = {"/": (200, HTML, {**HTML_HEADERS, "X-Test": "yes"})}
    with server(routes) as (base, _):
        r = fetch_one(base + "/")
    assert r.ok and r.outcome is FetchOutcome.OK
    assert r.url == r.final_url == base + "/"
    assert r.status_code == 200
    assert r.content_type == "text/html" and r.charset == "utf-8"
    assert r.headers["x-test"] == "yes"
    assert r.body == HTML and r.size == len(HTML)
    assert "hello" in r.text
    assert r.redirects == () and not r.redirected
    assert r.error is None
    assert r.duration > 0


def test_connection_reuse_with_shared_client(local_network):
    routes = {"/a": (200, HTML, HTML_HEADERS), "/b": (200, HTML, HTML_HEADERS), "/c": (200, HTML, HTML_HEADERS)}
    with server(routes) as (base, state):
        results = run_fetch([base + "/a", base + "/b", base + "/c"])
    assert all(r.ok for r in results)
    assert state.requests == ["/a", "/b", "/c"]
    assert state.connections == 1  # one TCP connection served three requests


def test_concurrent_fetches_share_one_client(local_network):
    routes = {f"/p{i}": (200, HTML, HTML_HEADERS) for i in range(6)}

    async def main(base):
        async with AsyncHttpClient() as client:
            return await asyncio.gather(*(client.fetch(f"{base}/p{i}") for i in range(6)))

    with server(routes) as (base, _):
        results = asyncio.run(main(base))
    assert all(r.ok for r in results)


# --- status codes ----------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429, 500, 502, 503])
def test_http_error_statuses(local_network, status):
    with server({"/x": (status, b"err", HTML_HEADERS)}) as (base, _):
        r = fetch_one(base + "/x")
    assert r.outcome is FetchOutcome.HTTP_ERROR and not r.ok
    assert r.status_code == status
    assert r.error is not None and r.error.detail == str(status)
    assert r.body == b""  # error bodies are not read


def test_2xx_statuses_are_ok(local_network):
    with server({"/created": (201, HTML, HTML_HEADERS), "/empty": (204, b"", {})}) as (base, _):
        created, empty = run_fetch([base + "/created", base + "/empty"])
    assert created.ok and created.status_code == 201
    assert empty.ok and empty.status_code == 204 and empty.body == b""


# --- redirects -------------------------------------------------------------------------------

def test_redirect_followed_and_recorded(local_network):
    routes = {
        "/old": (301, b"", {"Location": "/mid"}),
        "/mid": (302, b"", {"Location": "/new"}),
        "/new": (200, HTML, HTML_HEADERS),
    }
    with server(routes) as (base, _):
        r = fetch_one(base + "/old")
    assert r.ok and r.url == base + "/old" and r.final_url == base + "/new"
    assert [(h.url, h.status_code, h.location) for h in r.redirects] == [
        (base + "/old", 301, base + "/mid"),
        (base + "/mid", 302, base + "/new"),
    ]
    assert r.redirected


def test_redirects_disabled(local_network):
    routes = {"/old": (301, b"", {"Location": "/new"}), "/new": (200, HTML, HTML_HEADERS)}
    with server(routes) as (base, state):
        r = fetch_one(base + "/old", HttpClientConfig(follow_redirects=False))
    assert r.outcome is FetchOutcome.REDIRECT_NOT_FOLLOWED and r.status_code == 301
    assert state.requests == ["/old"]


def test_redirect_loop_and_limit(local_network):
    routes = {"/loop": (302, b"", {"Location": "/loop"}),
              "/a": (302, b"", {"Location": "/b"}), "/b": (302, b"", {"Location": "/a"}),
              **{f"/h{i}": (302, b"", {"Location": f"/h{i + 1}"}) for i in range(10)}}
    with server(routes) as (base, _):
        loop, cycle, chain = run_fetch(
            [base + "/loop", base + "/a", base + "/h0"], HttpClientConfig(max_redirects=3)
        )
    assert loop.outcome is FetchOutcome.REDIRECT_ERROR and loop.error.detail == "loop"
    assert cycle.outcome is FetchOutcome.REDIRECT_ERROR and cycle.error.detail == "loop"
    assert chain.outcome is FetchOutcome.REDIRECT_ERROR and chain.error.detail == "max_redirects"
    assert len(chain.redirects) == 4


def test_redirect_without_location_or_bad_scheme(local_network):
    routes = {"/nolocation": (302, b"", {}), "/ftp": (302, b"", {"Location": "ftp://example.com/x"})}
    with server(routes) as (base, _):
        a, b = run_fetch([base + "/nolocation", base + "/ftp"])
    assert a.outcome is FetchOutcome.REDIRECT_ERROR and a.error.detail == "no_location"
    assert b.outcome is FetchOutcome.REDIRECT_ERROR and b.error.detail == "bad_scheme"


def test_redirect_target_is_policy_checked(local_network):
    routes = {"/evil": (302, b"", {"Location": "http://169.254.169.254/latest"})}
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy)
    with server(routes) as (base, _):
        r = fetch_one(base + "/evil", evaluator=evaluator, resolver=lambda h: ["127.0.0.1"])
    assert r.outcome is FetchOutcome.BLOCKED
    assert len(r.redirects) == 1


# --- content types ---------------------------------------------------------------------------

@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/json", "text/plain"])
def test_unsupported_content_types(local_network, ctype):
    with server({"/f": (200, b"data", {"Content-Type": ctype})}) as (base, _):
        r = fetch_one(base + "/f")
    assert r.outcome is FetchOutcome.UNSUPPORTED_CONTENT_TYPE
    assert r.content_type == ctype and r.body == b"" and r.status_code == 200


def test_content_type_parsing_and_configuration(local_network):
    routes = {
        "/upper": (200, HTML, {"Content-Type": "TEXT/HTML; Charset=ISO-8859-1"}),
        "/xhtml": (200, HTML, {"Content-Type": "application/xhtml+xml"}),
        "/none": (200, HTML, {}),
        "/text": (200, b"plain", {"Content-Type": "text/plain"}),
    }
    with server(routes) as (base, _):
        upper, xhtml, none = run_fetch([base + "/upper", base + "/xhtml", base + "/none"])
        strict = fetch_one(base + "/none", HttpClientConfig(allow_missing_content_type=False))
        text = fetch_one(base + "/text", HttpClientConfig(supported_content_types=frozenset({"text/plain"})))
    assert upper.ok and upper.content_type == "text/html" and upper.charset == "iso-8859-1"
    assert xhtml.ok and none.ok and none.content_type is None
    assert strict.outcome is FetchOutcome.UNSUPPORTED_CONTENT_TYPE
    assert text.ok and text.body == b"plain"


# --- size limits -----------------------------------------------------------------------------

def test_declared_and_streamed_size_limits(local_network):
    routes = {
        "/big": (200, b"x" * 5000, HTML_HEADERS),
        "/stream": (200, b"x" * 5000, {**HTML_HEADERS, "X-No-Length": "1"}),
        "/small": (200, b"x" * 500, HTML_HEADERS),
    }
    with server(routes) as (base, _):
        big, stream, small = run_fetch(
            [base + "/big", base + "/stream", base + "/small"], HttpClientConfig(max_response_bytes=1000)
        )
    assert big.outcome is FetchOutcome.TOO_LARGE and big.body == b""
    assert stream.outcome is FetchOutcome.TOO_LARGE and stream.body == b""
    assert small.ok and small.size == 500


# --- failures never raise --------------------------------------------------------------------

def test_timeout(local_network):
    with server({}) as (base, _):
        r = fetch_one(base + "/slow", HttpClientConfig(read_timeout=0.3))
    assert r.outcome is FetchOutcome.TIMEOUT and r.error is not None
    assert r.status_code is None


def test_connection_refused(local_network):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    r = fetch_one(f"http://127.0.0.1:{port}/")
    assert r.outcome is FetchOutcome.CONNECTION_ERROR and r.error is not None


def test_one_failure_does_not_stop_later_fetches(local_network):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead = probe.getsockname()[1]
    probe.close()
    with server({"/ok": (200, HTML, HTML_HEADERS)}) as (base, _):
        results = run_fetch([f"http://127.0.0.1:{dead}/", "not a url", base + "/ok"])
    assert [r.outcome for r in results] == [
        FetchOutcome.CONNECTION_ERROR, FetchOutcome.INVALID_URL, FetchOutcome.OK,
    ]


@pytest.mark.parametrize(
    "url",
    ["", "   ", "not a url", "/relative/path", "//nohost/path", "ftp://example.com/", "http://",
     "http://host:99999/", "http://host:abc/", "mailto:a@b.com", "javascript:alert(1)"],
)
def test_invalid_urls_are_reported_not_raised(url):
    # No server needed: invalid URLs are rejected before any network activity.
    r = fetch_one(url)
    assert r.outcome is FetchOutcome.INVALID_URL
    assert r.error is not None and r.error.kind is FetchOutcome.INVALID_URL
    assert r.status_code is None and r.body == b""


def test_policy_blocks_private_addresses_before_connecting():
    evaluator = UrlEvaluator(UrlPolicy())  # default: private networks refused
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest", "http://localhost/"):
        assert fetch_one(url, evaluator=evaluator).outcome is FetchOutcome.BLOCKED


def test_dns_result_is_checked(monkeypatch):
    evaluator = UrlEvaluator(UrlPolicy())
    r = fetch_one("http://example.com/", evaluator=evaluator, resolver=lambda h: ["10.0.0.5"])
    assert r.outcome is FetchOutcome.BLOCKED and "resolves_to" in r.error.detail


def test_unexpected_exception_is_contained():
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("boom")

    r = fetch_one("http://example.com/", transport=httpx.MockTransport(handler))
    assert r.outcome is FetchOutcome.ERROR and r.error.detail == "RuntimeError"


def test_mock_transport_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/timeout":
            raise httpx.ReadTimeout("slow", request=request)
        raise httpx.ConnectError("refused", request=request)

    t = httpx.MockTransport(handler)
    timeout, refused = run_fetch(["http://example.com/timeout", "http://example.com/x"], transport=t)
    assert timeout.outcome is FetchOutcome.TIMEOUT
    assert refused.outcome is FetchOutcome.CONNECTION_ERROR


# --- configuration ---------------------------------------------------------------------------

def test_config_from_settings_and_defaults():
    s = Settings(_env_file=None)
    cfg = HttpClientConfig.from_settings(s)
    assert cfg.follow_redirects is True and cfg.max_redirects == 5
    assert cfg.connect_timeout == 10 and cfg.read_timeout == 15
    assert cfg.write_timeout == 10 and cfg.pool_timeout == 10
    assert cfg.supported_content_types == {"text/html", "application/xhtml+xml"}
    custom = HttpClientConfig.from_settings(
        Settings(_env_file=None, crawler_supported_content_types=" Text/HTML , text/plain ",
                 crawler_follow_redirects=False)
    )
    assert custom.supported_content_types == {"text/html", "text/plain"}
    assert custom.follow_redirects is False


def test_timeouts_are_applied_to_httpx_client():
    async def main():
        async with AsyncHttpClient(HttpClientConfig(connect_timeout=1, read_timeout=2, write_timeout=3, pool_timeout=4)) as c:
            t = c._client.timeout
            return t.connect, t.read, t.write, t.pool

    assert asyncio.run(main()) == (1, 2, 3, 4)


def test_settings_validation_and_docs_in_sync():
    from pathlib import Path

    from pydantic import ValidationError

    for bad in ({"crawler_connect_timeout": 0}, {"crawler_max_redirects": 99},
                {"crawler_max_response_bytes": 10}, {"crawler_user_agent": ""}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **bad)
    root = Path(__file__).parent.parent
    env, doc = (root / ".env.example").read_text(), (root / "docs" / "configuration.md").read_text()
    for name in Settings.model_fields:
        if name.startswith("crawler_"):
            assert f"APP_{name.upper()}=" in env, name
            assert f"`APP_{name.upper()}`" in doc, name


def test_client_is_closed_after_context():
    async def main():
        async with AsyncHttpClient() as c:
            inner = c._client
        return inner.is_closed

    assert asyncio.run(main()) is True
