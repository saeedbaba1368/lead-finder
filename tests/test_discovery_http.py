"""HttpFetcher against a real local HTTP server on loopback.

The suite-wide network guard (conftest) is lifted inside this module only, and only for
127.0.0.1; the policy is told to allow private networks so the loopback server is reachable.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.discovery import FetchStatus, HttpFetcher, SitemapDiscovery
from app.urls import UrlEvaluator, UrlPolicy

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_GETADDRINFO = socket.getaddrinfo

SITEMAP = (
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>http://127.0.0.1:{port}/page-1</loc></url></urlset>"
)


def make_handler(routes: dict[str, tuple[int, bytes, dict[str, str]]]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                time.sleep(1.5)
            status, body, headers = routes.get(self.path, (404, b"nope", {}))
            body = body.replace(b"{port}", str(self.server.server_port).encode())
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):  # client gave up (timeout test)
                pass

        def log_message(self, *args):
            pass

    return Handler


@contextmanager
def server(routes) -> Iterator[int]:
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(routes))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def local_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket.socket, "connect_ex", _REAL_CONNECT_EX)
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)


def make_fetcher(port: int, **kwargs) -> tuple[HttpFetcher, UrlEvaluator]:
    policy = UrlPolicy(ssrf_allow_private_networks=True, allowed_ports=None)
    evaluator = UrlEvaluator(policy, seed_url=f"http://127.0.0.1:{port}")
    return HttpFetcher(evaluator, resolver=lambda host: ["127.0.0.1"], **kwargs), evaluator


def test_status_codes(local_network):
    routes = {
        "/ok": (200, b"hello", {"Content-Type": "text/plain"}),
        "/forbidden": (403, b"", {}),
        "/error": (500, b"", {}),
        "/gone": (410, b"", {}),
        "/teapot": (418, b"", {}),
    }
    with server(routes) as port:
        fetch, _ = make_fetcher(port)
        base = f"http://127.0.0.1:{port}"
        assert fetch(f"{base}/ok").ok and fetch(f"{base}/ok").body == b"hello"
        assert fetch(f"{base}/missing").status is FetchStatus.NOT_FOUND
        assert fetch(f"{base}/gone").status is FetchStatus.NOT_FOUND
        assert fetch(f"{base}/forbidden").status is FetchStatus.FORBIDDEN
        assert fetch(f"{base}/error").status is FetchStatus.SERVER_ERROR
        assert fetch(f"{base}/teapot").status is FetchStatus.HTTP_ERROR


def test_timeout(local_network):
    with server({}) as port:
        fetch, _ = make_fetcher(port, timeout=0.3)
        assert fetch(f"http://127.0.0.1:{port}/slow").status is FetchStatus.TIMEOUT


def test_connection_refused(local_network):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens here any more
    fetch, _ = make_fetcher(port)
    assert fetch(f"http://127.0.0.1:{port}/robots.txt").status is FetchStatus.CONNECTION_ERROR


def test_body_size_cap(local_network):
    with server({"/big": (200, b"x" * 5000, {})}) as port:
        fetch, _ = make_fetcher(port, max_bytes=1000)
        assert fetch(f"http://127.0.0.1:{port}/big").status is FetchStatus.TOO_LARGE


def test_redirects_are_followed_and_validated(local_network):
    routes = {
        "/old": (301, b"", {"Location": "/new"}),
        "/new": (200, b"moved", {}),
        "/loop": (302, b"", {"Location": "/loop"}),
        "/evil": (302, b"", {"Location": "http://169.254.169.254/latest"}),
    }
    with server(routes) as port:
        fetch, _ = make_fetcher(port)
        base = f"http://127.0.0.1:{port}"
        assert fetch(f"{base}/old").body == b"moved"
        assert fetch(f"{base}/loop").status is FetchStatus.TOO_MANY_REDIRECTS
        assert fetch(f"{base}/evil").status is FetchStatus.BLOCKED


def test_ssrf_blocked_by_default_policy():
    evaluator = UrlEvaluator()  # default policy: loopback is refused, nothing is connected to
    fetch = HttpFetcher(evaluator, resolver=lambda host: ["127.0.0.1"])
    assert fetch("http://127.0.0.1/robots.txt").status is FetchStatus.BLOCKED
    assert fetch("https://example.com/robots.txt").status is FetchStatus.BLOCKED  # resolves to loopback


def test_end_to_end_with_local_server(local_network):
    with server({
        "/robots.txt": (200, b"User-agent: *\nSitemap: http://127.0.0.1:{port}/map.xml\n", {}),
        "/map.xml": (200, SITEMAP, {"Content-Type": "application/xml"}),
    }) as port:
        fetch, evaluator = make_fetcher(port)
        result = SitemapDiscovery(evaluator, fetch).discover(f"http://127.0.0.1:{port}")
        assert result.urls == [f"http://127.0.0.1:{port}/page-1"]


def test_end_to_end_fallback_with_local_server(local_network):
    with server({"/sitemap.xml": (200, SITEMAP, {})}) as port:
        fetch, evaluator = make_fetcher(port)
        result = SitemapDiscovery(evaluator, fetch).discover(f"http://127.0.0.1:{port}")
        assert result.used_fallback and result.urls == [f"http://127.0.0.1:{port}/page-1"]
