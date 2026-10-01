"""Host classification, registered-domain extraction and crawl-scope / subdomain policy."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from app.urls.errors import RejectReason
from app.urls.policy import UrlPolicy
from app.urls.suffixes import ALL_BUILTIN_SUFFIXES

# Subdomain labels that usually lead to people/contact/company pages.
RELEVANT_LABELS: frozenset[str] = frozenset(
    {
        "www", "about", "aboutus", "team", "teams", "people", "staff", "contact", "contacts",
        "company", "corporate", "careers", "career", "jobs", "blog", "news", "press",
        "investors", "investor", "ir", "faculty", "directory", "leadership", "management",
        "info", "home", "main", "global", "international", "en",
    }
)

# Subdomain labels that are infrastructure, not content. Blocking always wins.
BLOCKED_LABELS: frozenset[str] = frozenset(
    {
        "mail", "email", "webmail", "smtp", "imap", "pop", "pop3", "mx", "ns", "ns1", "ns2",
        "dns", "ftp", "sftp", "cdn", "static", "assets", "img", "images", "image", "media",
        "files", "download", "downloads", "api", "dev", "stage", "staging", "test", "qa",
        "uat", "sandbox", "demo", "git", "gitlab", "svn", "vpn", "remote", "admin", "cpanel",
        "whm", "autodiscover", "autoconfig", "status", "monitor", "grafana", "jenkins", "ci",
        "intranet", "internal", "login", "sso", "auth", "portal",
    }
)

_LOCALE_LABEL = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")
_WWW_LABEL = re.compile(r"^www\d*$")
_MAX_SUFFIX_LABELS = 3


def is_ip_host(host: str) -> bool:
    """True for a normalised IPv4 literal or a bracketed IPv6 literal."""
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def registered_domain(host: str, extra_suffixes: frozenset[str] = frozenset()) -> str | None:
    """Return the registrable domain (eTLD+1) of a normalised host.

    * IP literals and single-label hosts are returned unchanged.
    * Returns None when the host *is* a public suffix (e.g. `co.uk`, `github.io`).
    """
    if is_ip_host(host):
        return host
    labels = host.split(".")
    if len(labels) == 1:
        return host
    suffixes = ALL_BUILTIN_SUFFIXES | extra_suffixes
    suffix_len = 1
    for k in range(min(len(labels), _MAX_SUFFIX_LABELS), 1, -1):
        if ".".join(labels[-k:]) in suffixes:
            suffix_len = k
            break
    if len(labels) <= suffix_len:
        return None
    return ".".join(labels[-(suffix_len + 1):])


def subdomain_labels(host: str, reg_domain: str) -> tuple[str, ...]:
    """Labels of `host` to the left of its registered domain (empty for an apex host)."""
    if host == reg_domain:
        return ()
    return tuple(host[: -(len(reg_domain) + 1)].split("."))


def strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def matches_domain(host: str, domain: str) -> bool:
    """True if `host` equals `domain` or is a subdomain of it."""
    return host == domain or host.endswith("." + domain)


@dataclass(frozen=True, slots=True)
class ScopeResult:
    allowed: bool
    reason: RejectReason | None = None
    detail: str = ""


_OK = ScopeResult(True)


def is_relevant_subdomain(labels: tuple[str, ...], policy: UrlPolicy) -> bool:
    """`relevant` mode rule: no blocked label, and every label is allow-listed/www/locale."""
    if not labels:
        return True
    blocked = BLOCKED_LABELS | policy.extra_blocked_labels
    if any(label in blocked for label in labels):
        return False
    allowed = RELEVANT_LABELS | policy.extra_relevant_labels
    return all(
        label in allowed or _WWW_LABEL.match(label) or _LOCALE_LABEL.match(label)
        for label in labels
    )


def check_scope(host: str, seed_host: str, policy: UrlPolicy) -> ScopeResult:
    """Decide whether `host` may be crawled for a crawl that started at `seed_host`.

    Both hosts must be normalised. The seed host itself is always in scope (unless blocked).
    """
    for blocked in policy.blocked_domains:
        if matches_domain(host, blocked):
            return ScopeResult(False, RejectReason.BLOCKED_DOMAIN, blocked)

    if host == seed_host:
        return _OK
    if is_ip_host(host) or is_ip_host(seed_host):
        return ScopeResult(False, RejectReason.OUT_OF_SCOPE, "ip hosts only match exactly")

    extra = policy.extra_public_suffixes
    host_reg = registered_domain(host, extra)
    seed_reg = registered_domain(seed_host, extra)
    if host_reg is None:
        return ScopeResult(False, RejectReason.PUBLIC_SUFFIX, host)
    in_scope_domains = {seed_reg, *policy.scope_domains}
    if host_reg not in in_scope_domains:
        return ScopeResult(False, RejectReason.OUT_OF_SCOPE, host_reg)

    mode = policy.subdomain_mode
    www_equivalent = strip_www(host) == strip_www(seed_host)
    if mode == "exact":
        return ScopeResult(False, RejectReason.SUBDOMAIN_NOT_RELEVANT, "exact mode")
    if www_equivalent:
        return _OK
    if mode == "www":
        return ScopeResult(False, RejectReason.SUBDOMAIN_NOT_RELEVANT, "www mode")
    labels = subdomain_labels(host, host_reg)
    if mode == "all":
        return _OK
    if is_relevant_subdomain(labels, policy):
        return _OK
    return ScopeResult(False, RejectReason.SUBDOMAIN_NOT_RELEVANT, ".".join(labels))
