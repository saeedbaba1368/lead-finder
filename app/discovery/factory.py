"""Build a `SitemapDiscovery` from application settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.discovery.fetcher import Fetcher, HttpFetcher
from app.discovery.service import SitemapDiscovery
from app.urls.engine import UrlEvaluator
from app.urls.policy import UrlPolicy

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings


def build_discovery(
    settings: Settings, seed_url: str, fetcher: Fetcher | None = None
) -> SitemapDiscovery:
    """Create discovery for `seed_url` using the project URL policy and `APP_DISCOVERY_*`."""
    evaluator = UrlEvaluator(UrlPolicy.from_settings(settings), seed_url=seed_url)
    if fetcher is None:
        fetcher = HttpFetcher(
            evaluator,
            timeout=settings.discovery_timeout_seconds,
            max_bytes=settings.discovery_max_bytes,
            max_redirects=settings.discovery_max_redirects,
            user_agent=settings.discovery_user_agent,
        )
    return SitemapDiscovery(
        evaluator,
        fetcher,
        max_sitemap_bytes=settings.discovery_max_bytes,
        max_depth=settings.discovery_max_depth,
        max_sitemaps=settings.discovery_max_sitemaps,
        max_urls=settings.discovery_max_urls,
    )
