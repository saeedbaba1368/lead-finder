"""Sitemap discovery (Phase 5 (5.1-5.4)): robots.txt, Sitemap declarations, /sitemap.xml fallback,
`<urlset>` parsing and recursive `<sitemapindex>` traversal with cycle, depth and size limits."""

from app.discovery.factory import build_discovery
from app.discovery.fetcher import Fetcher, FetchResult, FetchStatus, HttpFetcher
from app.discovery.robots import RobotsResult, parse_robots
from app.discovery.service import DiscoveryResult, SitemapDiscovery, SitemapOutcome
from app.discovery.sitemap import (
    SitemapKind,
    SitemapParseResult,
    SitemapParseStatus,
    parse_sitemap,
)

__all__ = [
    "DiscoveryResult", "FetchResult", "FetchStatus", "Fetcher", "HttpFetcher", "RobotsResult",
    "SitemapDiscovery", "SitemapKind", "SitemapOutcome", "SitemapParseResult",
    "SitemapParseStatus", "build_discovery", "parse_robots", "parse_sitemap",
]
