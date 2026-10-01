"""HTTP crawler (Phase 6.1 async HTTP client, Phase 6.2 link discovery and BFS, Phase 7.1 HTML parsing)."""

from app.crawler.bfs import BfsCrawler, CrawledPage, CrawlResult, PageObserver, StopReason, build_crawler
from app.crawler.classify import PageCategory, PageClassification, classify_page
from app.crawler.html_parser import (
    Heading, PageMetadata, ParsedPage, PhoneNumber, decode_html, decode_html_with_encoding, is_html_content_type, parse_html, parse_response,
)
from app.crawler.http_client import AsyncHttpClient, HttpClientConfig
from app.crawler.language import LanguageDetection, detect_language
from app.crawler.links import LinkExtraction, extract_links
from app.crawler.metrics import ContentMetrics, compute_metrics
from app.crawler.models import FetchError, FetchOutcome, PageResult, RedirectHop
from app.crawler.lifecycle import CrawlLifecycle, CrawlTerminalError, is_resumable, lifecycle_of
from app.crawler.resume import CrawlPersistenceError
from app.crawler.social import SocialLink, extract_social_links
from app.crawler.structured import Address, StructuredBusiness
from app.crawler.state import CrawlStateReport, inspect_crawl_state

__all__ = [
    "Address", "AsyncHttpClient", "BfsCrawler", "ContentMetrics", "CrawlLifecycle", "CrawlPersistenceError", "CrawlResult", "CrawlStateReport", "CrawlTerminalError", "CrawledPage", "FetchError", "FetchOutcome",
    "Heading", "HttpClientConfig", "LanguageDetection", "LinkExtraction", "PageCategory", "PageClassification", "PageMetadata", "PageObserver", "PageResult", "ParsedPage", "PhoneNumber", "RedirectHop", "SocialLink", "StopReason", "StructuredBusiness",
    "build_crawler", "classify_page", "compute_metrics", "decode_html", "decode_html_with_encoding", "detect_language", "extract_links", "extract_social_links", "inspect_crawl_state", "is_html_content_type", "is_resumable",
    "lifecycle_of", "parse_html", "parse_response",
]
