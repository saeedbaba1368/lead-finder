"""URL engine and crawl policy (Phase 4). Pure logic: no network access, no crawling."""

from app.urls.canonical import CanonicalResult, extract_canonical_href, resolve_canonical
from app.urls.content import check_content_length, check_content_type, check_extension
from app.urls.dedupe import SeenUrls, dedupe_key
from app.urls.domain_identity import DomainIdentity, canonical_domain, domain_identity
from app.urls.domains import check_scope, registered_domain
from app.urls.engine import UrlDecision, UrlEvaluator, UrlGate
from app.urls.errors import RejectReason, UrlRejected
from app.urls.normalize import NormalizedUrl, normalize_url
from app.urls.policy import DEFAULT_POLICY, UrlPolicy
from app.urls.ssrf import (
    check_resolved_addresses,
    host_block_reason,
    ip_block_reason,
    resolve_and_check,
    system_resolver,
)
from app.urls.traps import TrapTracker, detect_stateless_trap

__all__ = [
    "DEFAULT_POLICY", "CanonicalResult", "DomainIdentity", "NormalizedUrl", "RejectReason", "SeenUrls",
    "TrapTracker", "UrlDecision", "UrlEvaluator", "UrlGate", "UrlPolicy", "UrlRejected",
    "canonical_domain", "check_content_length", "check_content_type", "check_extension", "check_resolved_addresses",
    "check_scope", "dedupe_key", "detect_stateless_trap", "domain_identity", "extract_canonical_href",
    "host_block_reason", "ip_block_reason", "normalize_url", "registered_domain",
    "resolve_and_check", "resolve_canonical", "system_resolver",
]
