"""URL normalisation.

`normalize_url` turns a raw (possibly relative) URL into one canonical string so that
equivalent URLs compare equal. It performs no network access. Steps:

* trim whitespace, drop tab/CR/LF, reject control characters, convert `\\` to `/`
* resolve against a base URL (relative links, protocol-relative links)
* accept only http/https; reject embedded credentials
* lower-case the scheme and host, IDNA-encode the host, strip one trailing dot,
  canonicalise IPv4 (decimal/octal/hex/short forms) and IPv6 literals
* drop default ports and enforce the allowed-port policy
* percent-encoding normalisation, dot-segment removal, duplicate-slash collapse,
  trailing-slash policy, `;jsessionid=` removal
* remove the fragment, drop known tracking/session query parameters, sort the query
"""

from __future__ import annotations

import ipaddress
import re
import string
from dataclasses import dataclass
from urllib.parse import unquote, unquote_plus, urljoin, urlsplit

from app.urls.errors import RejectReason, UrlRejected
from app.urls.policy import DEFAULT_POLICY, UrlPolicy

DEFAULT_PORTS = {"http": 80, "https": 443}

# Query parameters that carry only analytics/session state. Deliberately conservative:
# generic names such as `ref`, `source`, `campaign` or `sid` can change page content and
# are NOT stripped by default (add them via `extra_tracking_params` if desired).
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
        "twclid", "ttclid", "li_fat_id", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
        "__hssc", "__hstc", "__hsfp", "hsctatracking", "_ga", "_gl", "wt_mc", "cmpid",
        "mkt_tok", "vero_id", "vero_conv", "oly_anon_id", "oly_enc_id", "s_cid", "spm",
        "ref_src", "ref_url", "sr_share", "rb_clickid", "_openstat",
        # session identifiers
        "phpsessid", "jsessionid", "sessionid",
    }
)
TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "pk_", "mtm_", "hsa_")

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_PATH_SAFE = frozenset("/!$&'()*+,;=:@")
_QUERY_SAFE = frozenset("!$'()*+,;:@/?=")
_HEX = frozenset(string.hexdigits)
_STRIP_CHARS = "".join(chr(c) for c in range(0x21)) + "\x7f"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_NON_HTTP_SCHEME = re.compile(
    r"^(mailto|javascript|tel|data|file|ftp|sms|about|blob|callto|whatsapp):", re.IGNORECASE
)
_JSESSION = re.compile(r";jsessionid=[^;/?]*", re.IGNORECASE)
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    url: str
    scheme: str
    host: str  # lower-case ASCII; IPv4 dotted quad; IPv6 in brackets
    port: int | None  # None = scheme default
    path: str
    query: str

    @property
    def effective_port(self) -> int:
        return self.port if self.port is not None else DEFAULT_PORTS[self.scheme]

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(s for s in self.path.split("/") if s)

    @property
    def params(self) -> tuple[tuple[str, str], ...]:
        """Decoded (name, value) query pairs, in URL order."""
        return tuple(_decode_pair(tok) for tok in self.query.split("&") if tok)


def _decode_pair(token: str) -> tuple[str, str]:
    key, _, value = token.partition("=")
    return unquote_plus(key), unquote_plus(value)


def _reject(reason: RejectReason, detail: str = "") -> UrlRejected:
    return UrlRejected(reason, detail)


def _pct_normalize(text: str, safe: frozenset[str]) -> str:
    """Upper-case percent escapes, decode unreserved characters, encode everything unsafe."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "%":
            if i + 2 < n and text[i + 1] in _HEX and text[i + 2] in _HEX:
                byte = int(text[i + 1 : i + 3], 16)
                if byte == 0:
                    raise _reject(RejectReason.INVALID_URL, "null byte")
                ch = chr(byte)
                out.append(ch if ch in _UNRESERVED else f"%{byte:02X}")
                i += 3
                continue
            out.append("%25")
        elif c in _UNRESERVED or c in safe:
            out.append(c)
        else:
            try:
                out.extend(f"%{b:02X}" for b in c.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise _reject(RejectReason.INVALID_URL, "invalid unicode") from exc
        i += 1
    return "".join(out)


def _parse_ipv4_loose(host: str) -> ipaddress.IPv4Address:
    """Parse inet_aton-style IPv4 (`127.1`, `0x7f000001`, `2130706433`, `0177.0.0.1`)."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        raise _reject(RejectReason.INVALID_HOST, "bad numeric host")
    numbers: list[int] = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                digits = part[2:]
                if digits and not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    raise ValueError
                numbers.append(int(digits, 16) if digits else 0)
            elif len(part) > 1 and part.startswith("0"):
                if not re.fullmatch(r"[0-7]+", part):
                    raise ValueError
                numbers.append(int(part, 8))
            elif re.fullmatch(r"[0-9]+", part):
                numbers.append(int(part))
            else:
                raise ValueError
        except ValueError as exc:
            raise _reject(RejectReason.INVALID_HOST, "bad numeric host") from exc
    if any(x > 255 for x in numbers[:-1]) or numbers[-1] >= 256 ** (5 - len(numbers)):
        raise _reject(RejectReason.INVALID_HOST, "numeric host out of range")
    value = numbers[-1]
    for i, x in enumerate(numbers[:-1]):
        value += x << (8 * (3 - i))
    return ipaddress.IPv4Address(value)


def _normalize_host(raw_host: str) -> str:
    if not raw_host:
        raise _reject(RejectReason.INVALID_HOST, "missing host")
    if raw_host.startswith("["):
        inner = raw_host[1:-1]
        if not raw_host.endswith("]") or "%" in inner:
            raise _reject(RejectReason.INVALID_HOST, "bad IPv6 literal")
        try:
            return f"[{ipaddress.IPv6Address(inner).compressed}]"
        except ValueError as exc:
            raise _reject(RejectReason.INVALID_HOST, "bad IPv6 literal") from exc

    host = unquote(raw_host).lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _reject(RejectReason.INVALID_HOST, "bad IDNA host") from exc
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        raise _reject(RejectReason.INVALID_HOST, "bad host length")
    labels = host.split(".")
    if any(not _HOST_LABEL.match(label) for label in labels):
        raise _reject(RejectReason.INVALID_HOST, "invalid characters in host")
    if _NUMERIC_LABEL.match(labels[-1]):
        return str(_parse_ipv4_loose(host))
    return host


def _split_authority(netloc: str) -> tuple[str, int | None]:
    if "@" in netloc:
        raise _reject(RejectReason.CREDENTIALS_IN_URL)
    if netloc.startswith("["):
        end = netloc.find("]")
        if end == -1:
            raise _reject(RejectReason.INVALID_HOST, "unterminated IPv6 literal")
        host, rest = netloc[: end + 1], netloc[end + 1 :]
        if rest and not rest.startswith(":"):
            raise _reject(RejectReason.INVALID_HOST, "junk after IPv6 literal")
        port_text = rest[1:]
    else:
        host, _, port_text = netloc.partition(":")
        if ":" in port_text:
            raise _reject(RejectReason.INVALID_HOST, "multiple colons")
    port: int | None = None
    if port_text:
        if not re.fullmatch(r"[0-9]{1,5}", port_text):
            raise _reject(RejectReason.INVALID_PORT, port_text)
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise _reject(RejectReason.INVALID_PORT, port_text)
    return host, port


def _normalize_path(path: str, policy: UrlPolicy) -> str:
    path = _JSESSION.sub("", path or "/")
    if not path.startswith("/"):
        path = "/" + path
    path = _pct_normalize(path, _PATH_SAFE)
    segments = path.split("/")
    ends_with_slash = segments[-1] in ("", ".", "..")
    out: list[str] = []
    for seg in segments[1:]:
        if seg == ".":
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    if segments[-1] == "" and out and out[-1] == "":
        out.pop()
    if policy.collapse_slashes:
        out = [s for s in out if s]
    result = "/" + "/".join(out)
    if ends_with_slash and out and policy.trailing_slash == "keep":
        result += "/"
    return result


def is_tracking_param(name: str, policy: UrlPolicy) -> bool:
    lowered = name.lower()
    return (
        lowered in TRACKING_PARAMS
        or lowered in policy.extra_tracking_params
        or lowered.startswith(TRACKING_PREFIXES)
    )


def _normalize_query(query: str, policy: UrlPolicy) -> str:
    if not query:
        return ""
    pairs: list[tuple[str, str]] = []
    for token in query.split("&"):
        if not token:
            continue
        key, sep, value = token.partition("=")
        norm_key = _pct_normalize(key, _QUERY_SAFE)
        if policy.strip_tracking_params and is_tracking_param(unquote_plus(norm_key), policy):
            continue
        norm_value = _pct_normalize(value, _QUERY_SAFE)
        pairs.append((norm_key, f"{norm_key}{sep}{norm_value}"))
    if policy.sort_query:
        pairs.sort(key=lambda p: p[0])  # stable: repeated keys keep their order
    return "&".join(p[1] for p in pairs)


def normalize_url(
    raw: str,
    base: str | None = None,
    policy: UrlPolicy = DEFAULT_POLICY,
    *,
    default_scheme: str | None = None,
) -> NormalizedUrl:
    """Normalise `raw` (optionally relative to `base`). Raises `UrlRejected` if unusable.

    `default_scheme` (e.g. "https") lets scheme-less seeds such as `example.com/team` through.
    """
    if not isinstance(raw, str):
        raise _reject(RejectReason.INVALID_URL, "not a string")
    text = raw.strip(_STRIP_CHARS)
    if not text:
        raise _reject(RejectReason.EMPTY)
    text = text.replace("\t", "").replace("\r", "").replace("\n", "")
    if len(text) > policy.max_url_length:
        raise _reject(RejectReason.TOO_LONG, str(len(text)))
    if _CONTROL.search(text):
        raise _reject(RejectReason.INVALID_URL, "control character")

    # Browsers treat `\` like `/`; doing the same avoids parser-differential attacks such
    # as `http://good.example\@evil.example/`.
    cut = min((i for i in (text.find("?"), text.find("#")) if i != -1), default=len(text))
    text = text[:cut].replace("\\", "/") + text[cut:]

    if base is not None:
        try:
            text = urljoin(base, text)
        except ValueError as exc:
            raise _reject(RejectReason.INVALID_URL, "unresolvable against base") from exc
    elif default_scheme and not text.startswith(("http://", "https://")):
        if text.startswith("//"):
            text = f"{default_scheme}:{text}"
        elif not _NON_HTTP_SCHEME.match(text):
            text = f"{default_scheme}://{text.lstrip('/')}"

    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise _reject(RejectReason.INVALID_URL, "unparseable") from exc

    scheme = parts.scheme.lower()
    if not scheme or not _SCHEME.match(scheme + ":"):
        raise _reject(RejectReason.INVALID_URL, "missing scheme")
    if scheme not in DEFAULT_PORTS:
        raise _reject(RejectReason.UNSUPPORTED_SCHEME, scheme)

    raw_host, port = _split_authority(parts.netloc)
    host = _normalize_host(raw_host)
    if port is not None and port == DEFAULT_PORTS[scheme]:
        port = None
    effective = port if port is not None else DEFAULT_PORTS[scheme]
    if policy.allowed_ports is not None and effective not in policy.allowed_ports:
        raise _reject(RejectReason.PORT_NOT_ALLOWED, str(effective))

    path = _normalize_path(parts.path, policy)
    query = _normalize_query(parts.query, policy)  # the fragment is dropped entirely

    authority = host if port is None else f"{host}:{port}"
    url = f"{scheme}://{authority}{path}" + (f"?{query}" if query else "")
    if len(url) > policy.max_url_length:
        raise _reject(RejectReason.TOO_LONG, str(len(url)))
    return NormalizedUrl(url=url, scheme=scheme, host=host, port=port, path=path, query=query)
