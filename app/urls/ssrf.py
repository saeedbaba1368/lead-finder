"""SSRF protection: block loopback, private, link-local, reserved and cloud-metadata targets.

Two layers:
1. `host_block_reason` -- static check on the (normalised) host of a URL. No DNS.
2. `check_resolved_addresses` / `resolve_and_check` -- checks the addresses a hostname
   *resolves to*, defeating names that point at internal IPs. The resolver is injected, so
   tests never touch the network. The future fetcher must connect to a validated address
   (or re-validate at connect time) to avoid DNS-rebinding races.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence

from app.urls.errors import RejectReason, UrlRejected
from app.urls.policy import UrlPolicy

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Sequence[str]]

BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "broadcasthost",
        "metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal",
        "kubernetes", "kubernetes.default", "kubernetes.default.svc",
    }
)
BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (
    ".localhost", ".local", ".localdomain", ".internal", ".intranet", ".lan", ".home.arpa",
    ".corp", ".home", ".private", ".svc", ".cluster.local", ".in-addr.arpa", ".ip6.arpa",
)
# Public wildcard-DNS services that resolve `anything.<ip>.nip.io` to that IP (often 127.0.0.1).
WILDCARD_DNS_SUFFIXES: tuple[str, ...] = (
    "nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me", "vcap.me", "lacolhost.com",
    "traefik.me", "localho.st",
)

_METADATA_ADDRESSES: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure / OpenStack / DigitalOcean IMDS
        "169.254.170.2",  # AWS ECS task metadata
        "100.100.100.200",  # Alibaba Cloud
        "168.63.129.16",  # Azure WireServer (public address space, so listed explicitly)
        "192.0.0.192",  # Oracle Cloud
        "fd00:ec2::254",  # AWS IMDS over IPv6
    }
)

_NETWORKS: tuple[tuple[str, str], ...] = (
    ("0.0.0.0/8", "unspecified"),
    ("10.0.0.0/8", "private"),
    ("100.64.0.0/10", "cgnat"),
    ("127.0.0.0/8", "loopback"),
    ("169.254.0.0/16", "link_local"),
    ("172.16.0.0/12", "private"),
    ("192.0.0.0/24", "reserved"),
    ("192.0.2.0/24", "documentation"),
    ("192.88.99.0/24", "reserved"),
    ("192.168.0.0/16", "private"),
    ("198.18.0.0/15", "benchmarking"),
    ("198.51.100.0/24", "documentation"),
    ("203.0.113.0/24", "documentation"),
    ("224.0.0.0/4", "multicast"),
    ("240.0.0.0/4", "reserved"),
    ("::/128", "unspecified"),
    ("::1/128", "loopback"),
    ("100::/64", "reserved"),
    ("2001::/32", "teredo"),
    ("2001:db8::/32", "documentation"),
    ("fc00::/7", "private"),
    ("fe80::/10", "link_local"),
    ("fec0::/10", "private"),
    ("ff00::/8", "multicast"),
)
_PARSED_NETWORKS = tuple((ipaddress.ip_network(n), r) for n, r in _NETWORKS)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIIT = ipaddress.ip_network("::ffff:0:0:0/96")  # IPv4-translated (RFC 2765), not IPv4-mapped
_V4_COMPAT = ipaddress.ip_network("::/96")


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 address hidden inside an IPv6 one (mapped, translated, NAT64, 6to4, IPv4-compatible)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64 or ip in _SIIT:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _V4_COMPAT and int(ip) > 1:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def ip_block_reason(ip: IPAddress) -> str | None:
    """Return why an IP must not be contacted, or None if it is a public unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            return ip_block_reason(embedded) or None
    if str(ip) in _METADATA_ADDRESSES:
        return "metadata"
    for network, reason in _PARSED_NETWORKS:
        if ip.version == network.version and ip in network:
            return reason
    if not ip.is_global:
        return "non_global"
    return None


def _parse_ip(text: str) -> IPAddress | None:
    candidate = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    candidate = candidate.split("%", 1)[0]  # drop IPv6 zone id
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def host_block_reason(host: str, policy: UrlPolicy) -> str | None:
    """Static SSRF check for a normalised host. Returns a short reason or None if allowed."""
    ip = _parse_ip(host)
    if ip is not None:
        reason = ip_block_reason(ip)
    else:
        reason = _hostname_block_reason(host, policy)
    if reason is None:
        return None
    if reason == "metadata":
        return reason  # cloud metadata is blocked even when private networks are allowed
    if policy.ssrf_allow_private_networks:
        return None
    return reason


def _hostname_block_reason(host: str, policy: UrlPolicy) -> str | None:
    if host in BLOCKED_HOSTNAMES:
        return "internal_hostname"
    if host.endswith(BLOCKED_HOST_SUFFIXES):
        return "internal_hostname"
    for suffix in WILDCARD_DNS_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return "wildcard_dns"
    if "." not in host and policy.block_single_label_hosts:
        return "single_label"
    return None


def check_resolved_addresses(
    addresses: Sequence[str], policy: UrlPolicy
) -> str | None:
    """Reason to block if *any* resolved address is unsafe (or none/invalid), else None."""
    if not addresses:
        return "unresolvable"
    for text in addresses:
        ip = _parse_ip(text)
        if ip is None:
            return "invalid_address"
        reason = ip_block_reason(ip)
        if reason is None:
            continue
        if reason == "metadata" or not policy.ssrf_allow_private_networks:
            return reason
    return None


def resolve_and_check(host: str, resolver: Resolver, policy: UrlPolicy) -> list[str]:
    """Resolve `host` via the injected `resolver` and validate every address.

    Returns the validated addresses (so the caller can pin the connection to one of them).
    Raises UrlRejected(SSRF_BLOCKED) otherwise. IP-literal hosts skip resolution.
    """
    static = host_block_reason(host, policy)
    if static is not None:
        raise UrlRejected(RejectReason.SSRF_BLOCKED, static)
    if _parse_ip(host) is not None:
        return [host.strip("[]")]
    try:
        addresses = list(resolver(host))
    except OSError as exc:
        raise UrlRejected(RejectReason.SSRF_BLOCKED, "unresolvable") from exc
    reason = check_resolved_addresses(addresses, policy)
    if reason is not None:
        raise UrlRejected(RejectReason.SSRF_BLOCKED, f"resolves_to_{reason}")
    return addresses


def system_resolver(host: str) -> list[str]:
    """Resolver backed by the OS (performs real DNS). Never used by the test-suite."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in seen:
            seen.append(address)
    return seen
