"""Application settings, loaded from environment variables and an optional .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    name: str = "leadfinder"
    env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    database_url: str = "sqlite:///./leadfinder.db"
    db_echo: bool = False

    # --- URL policy (Phase 4). Comma-separated strings keep env values simple. ---
    url_max_length: int = Field(default=2048, ge=64, le=65536)
    url_allowed_ports: str = "80,443"  # "*" or empty = any port
    url_subdomain_mode: Literal["exact", "www", "relevant", "all"] = "relevant"
    url_scope_domains: str = ""
    url_blocked_domains: str = ""
    url_extra_tracking_params: str = ""
    url_extra_public_suffixes: str = ""
    url_trailing_slash: Literal["strip", "keep"] = "strip"
    url_allow_pdf: bool = False
    url_allow_plain_text: bool = False
    url_ssrf_allow_private_networks: bool = False
    url_max_path_depth: int = Field(default=12, ge=1)
    url_max_query_params: int = Field(default=12, ge=1)
    url_max_page_number: int = Field(default=50, ge=1)
    url_max_query_variants_per_path: int = Field(default=30, ge=1)
    url_max_calendar_urls_per_pattern: int = Field(default=40, ge=1)
    url_calendar_years_back: int = Field(default=5, ge=0)
    url_calendar_years_ahead: int = Field(default=1, ge=0)

    # --- Sitemap discovery (Phase 5.1) ---
    discovery_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    discovery_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    discovery_max_depth: int = Field(default=5, ge=0, le=20)
    discovery_max_sitemaps: int = Field(default=1000, ge=1, le=100_000)
    discovery_max_urls: int = Field(default=100_000, ge=1, le=5_000_000)
    discovery_max_redirects: int = Field(default=3, ge=0, le=10)
    discovery_user_agent: str = Field(default="leadfinder/0.5 (+sitemap-discovery)", min_length=1)

    # --- HTTP crawler client (Phase 6.1) ---
    crawler_connect_timeout: float = Field(default=10.0, gt=0, le=120)
    crawler_read_timeout: float = Field(default=15.0, gt=0, le=300)
    crawler_write_timeout: float = Field(default=10.0, gt=0, le=120)
    crawler_pool_timeout: float = Field(default=10.0, gt=0, le=120)
    crawler_follow_redirects: bool = True
    crawler_max_redirects: int = Field(default=5, ge=0, le=20)
    crawler_max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)
    crawler_supported_content_types: str = "text/html,application/xhtml+xml"
    crawler_allow_missing_content_type: bool = True
    crawler_user_agent: str = Field(default="leadfinder/0.6 (+http-crawler)", min_length=1)
    crawler_max_connections: int = Field(default=20, ge=1, le=1000)
    crawler_max_keepalive_connections: int = Field(default=10, ge=0, le=1000)

    # --- BFS crawl limits (Phase 6.2) ---
    crawler_max_pages: int = Field(default=100, ge=1, le=100_000)
    crawler_max_depth: int = Field(default=3, ge=0, le=50)

    # --- Crawl controls (Phase 6.3.1) ---
    crawler_max_crawl_time: float = Field(default=0.0, ge=0, le=86_400)  # seconds; 0 = no limit
    crawler_request_delay: float = Field(default=0.0, ge=0, le=3_600)  # seconds between request starts

    @model_validator(mode="after")
    def _no_private_crawling_in_production(self) -> "Settings":
        if self.env == "production" and self.url_ssrf_allow_private_networks:
            raise ValueError("APP_URL_SSRF_ALLOW_PRIVATE_NETWORKS must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
