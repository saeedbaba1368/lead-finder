"""Configurable URL policy: one immutable object that every URL component reads."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

SubdomainMode = Literal["exact", "www", "relevant", "all"]
TrailingSlash = Literal["strip", "keep"]


def csv_set(value: str | None) -> frozenset[str]:
    """Parse a comma-separated string into a lower-cased set (blank entries dropped)."""
    if not value:
        return frozenset()
    return frozenset(p.strip().lower() for p in value.split(",") if p.strip())


def parse_ports(value: str | None) -> frozenset[int] | None:
    """`"80,443"` -> {80, 443}; `"*"` or `""` -> None (any port)."""
    if value is None or value.strip() in ("", "*"):
        return None
    ports = frozenset(int(p) for p in value.split(",") if p.strip())
    if any(not 1 <= p <= 65535 for p in ports):
        raise ValueError("ports must be within 1-65535")
    return ports


_POSITIVE_FIELDS = (
    "max_url_length", "max_path_depth", "max_segment_repeats", "max_query_params",
    "max_page_number", "max_offset", "max_query_variants_per_path",
    "max_calendar_urls_per_pattern", "max_content_length",
)


@dataclass(frozen=True, slots=True)
class UrlPolicy:
    # --- normalisation ---
    max_url_length: int = 2048
    allowed_ports: frozenset[int] | None = frozenset({80, 443})  # None = any port
    strip_tracking_params: bool = True
    extra_tracking_params: frozenset[str] = frozenset()
    sort_query: bool = True
    trailing_slash: TrailingSlash = "strip"
    collapse_slashes: bool = True

    # --- domain scope ---
    subdomain_mode: SubdomainMode = "relevant"
    extra_relevant_labels: frozenset[str] = frozenset()
    extra_blocked_labels: frozenset[str] = frozenset()
    scope_domains: frozenset[str] = frozenset()  # extra registered domains in scope
    blocked_domains: frozenset[str] = frozenset()  # never crawled (subdomains included)
    extra_public_suffixes: frozenset[str] = frozenset()

    # --- content eligibility ---
    allow_pdf: bool = False
    allow_plain_text: bool = False
    allow_missing_content_type: bool = True
    max_content_length: int = 10 * 1024 * 1024

    # --- SSRF ---
    ssrf_allow_private_networks: bool = False  # never unblocks cloud-metadata addresses
    block_single_label_hosts: bool = True

    # --- traps ---
    max_path_depth: int = 12
    max_segment_repeats: int = 3
    max_query_params: int = 12
    max_page_number: int = 50
    max_offset: int = 2500
    max_query_variants_per_path: int = 30
    max_calendar_urls_per_pattern: int = 40
    calendar_years_back: int = 5
    calendar_years_ahead: int = 1

    # --- canonical URLs ---
    canonical_allow_cross_domain: bool = False
    canonical_ignore_root: bool = True

    # --- duplicate detection ---
    dedupe_ignore_scheme: bool = True
    dedupe_ignore_www: bool = True
    dedupe_strip_index_documents: bool = True

    def __post_init__(self) -> None:
        for name in _POSITIVE_FIELDS:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.calendar_years_back < 0 or self.calendar_years_ahead < 0:
            raise ValueError("calendar year windows must be >= 0")
        if self.max_segment_repeats < 2:
            raise ValueError("max_segment_repeats must be >= 2")
        if self.subdomain_mode not in ("exact", "www", "relevant", "all"):
            raise ValueError(f"invalid subdomain_mode: {self.subdomain_mode}")
        if self.trailing_slash not in ("strip", "keep"):
            raise ValueError(f"invalid trailing_slash: {self.trailing_slash}")

    def with_changes(self, **changes: Any) -> UrlPolicy:
        """Return a copy with the given fields replaced (validated again)."""
        return replace(self, **changes)

    @classmethod
    def from_settings(cls, settings: Settings) -> UrlPolicy:
        """Build a policy from `APP_URL_*` settings."""
        return cls(
            max_url_length=settings.url_max_length,
            allowed_ports=parse_ports(settings.url_allowed_ports),
            extra_tracking_params=csv_set(settings.url_extra_tracking_params),
            trailing_slash=settings.url_trailing_slash,
            subdomain_mode=settings.url_subdomain_mode,
            scope_domains=csv_set(settings.url_scope_domains),
            blocked_domains=csv_set(settings.url_blocked_domains),
            extra_public_suffixes=csv_set(settings.url_extra_public_suffixes),
            allow_pdf=settings.url_allow_pdf,
            allow_plain_text=settings.url_allow_plain_text,
            ssrf_allow_private_networks=settings.url_ssrf_allow_private_networks,
            max_path_depth=settings.url_max_path_depth,
            max_query_params=settings.url_max_query_params,
            max_page_number=settings.url_max_page_number,
            max_query_variants_per_path=settings.url_max_query_variants_per_path,
            max_calendar_urls_per_pattern=settings.url_max_calendar_urls_per_pattern,
            calendar_years_back=settings.url_calendar_years_back,
            calendar_years_ahead=settings.url_calendar_years_ahead,
        )


DEFAULT_POLICY = UrlPolicy()
