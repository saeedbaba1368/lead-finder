"""Canonical website / domain identity (Phase 9.1). Pure logic: no network, no clock, no configuration.

`domain_identity(value)` turns a website written in any common way into one `DomainIdentity`, so that every spelling of
the same site gives the identical result. `canonical_domain(value)` is its identity key alone.

Two things are canonicalised, and they are deliberately different:

* `website`: the canonical URL of the site as given (`normalize_url`): lower-case scheme and host, IDNA (punycode)
  host, trailing host dot removed, default port (80 / 443) removed, fragment removed, tracking parameters removed,
  dot segments / duplicate slashes / trailing slash normalised. The path is **kept**: `https://Acme.example/en/team/`
  is `https://acme.example/en/team`. A leading `www.` is part of the URL and is **kept** here, because it is the
  address that is fetched.
* `domain`: the identity key, the part that names the *site* rather than one of its pages:
  - the registrable domain (eTLD+1) of the host, so `www.`, `shop.` and every other subdomain fall away
    (`WWW.Acme.co.uk` -> `acme.co.uk`); scheme, port, path, query and fragment never take part;
  - `www.` is therefore never part of an identity, whether the host is `www.acme.example`, `www2.acme.example`
    or a public suffix such as `www.co.uk` (-> `co.uk`) / `www.github.io` (-> `github.io`), so that `www.X` and `X`
    always agree; a real domain whose name merely starts with `www` (`www.com`) is left alone;
  - an IP literal is its own identity (IPv4 canonical dotted quad, IPv6 compressed **without** brackets, which is what
    the stored lead domains have always been);
  - a single-label host (`localhost`) or a host that *is* a public suffix (`co.uk`) is its own identity.

`value` may be a full URL, a scheme-less URL (`Acme.example/team`, scheme `default_scheme`, `https` unless given), a
protocol-relative URL (`//acme.example`) or a bare host (`ACME.example.`, a bare IPv6 address). Only `http` and `https`
exist; credentials in the URL are refused. Every unusable value raises `UrlRejected`.

The port policy of the crawl is **not** applied (`WEBSITE_POLICY` allows any port): a website is data, not a crawl
target. Public suffixes beyond the built-in list can be passed as `extra_suffixes`; the default is none, so the identity
never depends on configuration unless the caller asks for it.

Not here, on purpose: comparing / de-duplicating leads, lookups, persistence.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

from app.urls.domains import is_ip_host, registered_domain
from app.urls.errors import RejectReason, UrlRejected
from app.urls.normalize import normalize_url
from app.urls.policy import UrlPolicy

WEBSITE_POLICY = UrlPolicy(allowed_ports=None)  # any port: a website is data, not a crawl target
DEFAULT_SCHEME = "https"

_EXPLICIT_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_WWW = "www."


@dataclass(frozen=True, slots=True)
class DomainIdentity:
    domain: str  # the identity key (see module docstring)
    host: str  # canonical host of the website: lower-case, punycode, no trailing dot, `www.` kept, IPv6 bracketed
    website: str  # canonical URL, path kept (e.g. `https://www.acme.example/en/team`)
    scheme: str  # `http` or `https`
    port: int | None  # None = the scheme's default port
    path: str  # canonical path, always starts with `/`

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "host": self.host,
            "website": self.website,
            "scheme": self.scheme,
            "port": self.port,
            "path": self.path,
        }


def identity_of_host(host: str, extra_suffixes: frozenset[str] = frozenset()) -> str:
    """Identity key of an already normalised host (`NormalizedUrl.host`). See the module docstring for the rules."""
    if is_ip_host(host):
        return host[1:-1] if host.startswith("[") else host
    registered = registered_domain(host, extra_suffixes)
    if registered is None:  # the host is itself a public suffix
        return host
    if registered.startswith(_WWW):
        rest = registered[len(_WWW):]
        # `www.<public suffix>` is the suffix itself; `www.com` (a real domain) has no dot in `rest` and stays.
        if "." in rest and registered_domain(rest, extra_suffixes) is None:
            return rest
    return registered


def domain_identity(
    value: object,
    *,
    default_scheme: str | None = DEFAULT_SCHEME,
    extra_suffixes: frozenset[str] = frozenset(),
) -> DomainIdentity:
    """Canonical identity of the website `value`. Raises `UrlRejected` if it is not a usable http(s) website.

    `default_scheme=None` makes a scheme-less value an error (strict mode).
    """
    if not isinstance(value, str):
        raise UrlRejected(RejectReason.INVALID_URL, "not a string")
    text = value.strip()
    bare_ipv6 = _bare_ipv6(text)
    if bare_ipv6 is not None:  # `::1` / `2001:db8::1`: not a URL, but how an IPv6 identity is written
        text = f"[{bare_ipv6}]"
    # normalize_url's own `default_scheme` test is case-sensitive (`HTTP://x` would be taken for a host), so a value
    # that already carries a scheme is passed through untouched and the default is only used when there is none.
    scheme_default = None if _EXPLICIT_SCHEME.match(text) else default_scheme
    parsed = normalize_url(text, policy=WEBSITE_POLICY, default_scheme=scheme_default)
    return DomainIdentity(
        domain=identity_of_host(parsed.host, extra_suffixes),
        host=parsed.host,
        website=parsed.url,
        scheme=parsed.scheme,
        port=parsed.port,
        path=parsed.path,
    )


def canonical_domain(
    value: object,
    *,
    default_scheme: str | None = DEFAULT_SCHEME,
    extra_suffixes: frozenset[str] = frozenset(),
) -> str:
    """The identity key of `value` (see `domain_identity`)."""
    return domain_identity(value, default_scheme=default_scheme, extra_suffixes=extra_suffixes).domain


def _bare_ipv6(text: str) -> str | None:
    if ":" not in text or "/" in text or "[" in text:
        return None
    try:
        return ipaddress.IPv6Address(text).compressed
    except ValueError:
        return None
