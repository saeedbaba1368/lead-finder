from pathlib import Path

from app.core.config import Settings, get_settings
from app.discovery import FetchResult, FetchStatus, build_discovery

ROOT = Path(__file__).parent.parent


def test_env_example_documents_every_discovery_setting():
    text = (ROOT / ".env.example").read_text()
    for name in Settings.model_fields:
        if name.startswith("discovery_"):
            assert f"APP_{name.upper()}=" in text, name


def test_configuration_doc_documents_every_discovery_setting():
    text = (ROOT / "docs" / "configuration.md").read_text()
    for name in Settings.model_fields:
        if name.startswith("discovery_"):
            assert f"`APP_{name.upper()}`" in text, name


def test_defaults_and_validation():
    import pytest
    from pydantic import ValidationError

    s = Settings()
    assert s.discovery_timeout_seconds == 10 and s.discovery_max_redirects == 3
    for bad in ({"discovery_timeout_seconds": 0}, {"discovery_max_bytes": 10},
                {"discovery_max_redirects": 99}, {"discovery_user_agent": ""}):
        with pytest.raises(ValidationError):
            Settings(**bad)


def test_build_discovery_from_settings_with_injected_fetcher():
    body = b"<urlset><url><loc>https://example.com/p</loc></url></urlset>"

    def fetch(url: str) -> FetchResult:
        if url.endswith("/sitemap.xml"):
            return FetchResult(url, FetchStatus.OK, 200, body)
        return FetchResult(url, FetchStatus.NOT_FOUND, 404)

    result = build_discovery(get_settings(), "example.com", fetch).discover("example.com")
    assert result.urls == ["https://example.com/p"] and result.used_fallback


def test_phase_52_limit_settings():
    import pytest
    from pydantic import ValidationError

    s = Settings()
    assert s.discovery_max_sitemaps == 1000 and s.discovery_max_urls == 100_000
    assert s.discovery_max_depth == 5
    for bad in ({"discovery_max_sitemaps": 0}, {"discovery_max_urls": 0},
                {"discovery_max_depth": -1}, {"discovery_max_depth": 21}):
        with pytest.raises(ValidationError):
            Settings(**bad)


def test_build_discovery_applies_limits(monkeypatch):
    monkeypatch.setenv("APP_DISCOVERY_MAX_URLS", "1")
    get_settings.cache_clear()
    body = b"<urlset><url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b</loc></url></urlset>"

    def fetch(url: str) -> FetchResult:
        ok = url.endswith("/sitemap.xml")
        return FetchResult(url, FetchStatus.OK if ok else FetchStatus.NOT_FOUND, 200 if ok else 404, body if ok else b"")

    result = build_discovery(get_settings(), "example.com", fetch).discover("example.com")
    assert result.urls == ["https://example.com/a"] and result.url_limit_reached


def test_build_discovery_applies_depth_limit(monkeypatch):
    monkeypatch.setenv("APP_DISCOVERY_MAX_DEPTH", "0")
    get_settings.cache_clear()
    idx = b"<sitemapindex><sitemap><loc>https://example.com/a.xml</loc></sitemap></sitemapindex>"

    def fetch(url: str) -> FetchResult:
        if url.endswith("/sitemap.xml"):
            return FetchResult(url, FetchStatus.OK, 200, idx)
        return FetchResult(url, FetchStatus.NOT_FOUND, 404)

    result = build_discovery(get_settings(), "example.com", fetch).discover("example.com")
    assert result.depth_limit_reached and result.skipped_by_depth == 1
