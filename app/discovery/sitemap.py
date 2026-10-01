"""Safe parsing of XML sitemaps: `<urlset>` (page URLs) and `<sitemapindex>` (child sitemaps).

The type is decided by the XML root element, never by the file name. Safety (Phase 5.4):

* the document is parsed with expat directly (streaming, no tree is built, no recursion);
* the parser itself refuses any DTD: `<!DOCTYPE`, entity declarations and external entity
  references raise inside expat regardless of the input encoding, so external entities,
  entity expansion (billion laughs) and DTD retrieval are impossible; parameter-entity parsing
  is switched off as well;
* a cheap byte/text pre-check keeps the historical `UNSAFE` verdict for obvious DOCTYPE/ENTITY
  payloads (defence in depth);
* input size is capped and every failure is returned as a status rather than raised.

Following an index's children is the service's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from xml.parsers import expat

from app.discovery.robots import decode_text

MAX_SITEMAP_BYTES = 5 * 1024 * 1024
MAX_LOCS = 50_000  # sitemaps.org limit per file

_UNSAFE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_UNSAFE_WIDE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class SitemapParseStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    MALFORMED = "malformed"
    UNSAFE = "unsafe"
    TOO_LARGE = "too_large"
    UNSUPPORTED = "unsupported"  # unknown root element


class SitemapKind(StrEnum):
    URLSET = "urlset"
    INDEX = "sitemapindex"


@dataclass(slots=True)
class SitemapParseResult:
    status: SitemapParseStatus
    kind: SitemapKind | None = None  # set when status is OK
    locs: list[str] = field(default_factory=list)  # raw <loc> values (non-empty, stripped)
    empty_locs: int = 0
    missing_locs: int = 0  # <url>/<sitemap> entries with no <loc> element at all
    truncated: bool = False
    detail: str = ""


class _UnsafeXml(Exception):
    """Raised from inside expat when the document tries to use a DTD / entity feature."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _Collector:
    """Streaming expat handlers that collect `<loc>` values from direct `<url>`/`<sitemap>` children."""

    def __init__(self, max_locs: int) -> None:
        self.max_locs = max_locs
        self.root: str | None = None
        self.result = SitemapParseResult(SitemapParseStatus.OK)
        self._depth = 0
        self._entry: str | None = None  # expected entry name for the root kind, if known
        self._in_entry = False
        self._loc_seen = False
        self._collecting = False
        self._buf: list[str] = []

    def start(self, name: str, _attrs: dict[str, str]) -> None:
        self._depth += 1
        local = _local(name)
        if self._depth == 1:
            self.root = local
            if local == "urlset":
                self.result.kind, self._entry = SitemapKind.URLSET, "url"
            elif local == "sitemapindex":
                self.result.kind, self._entry = SitemapKind.INDEX, "sitemap"
        elif self._entry is None:
            return  # unknown root: keep parsing (to tell malformed from unsupported), collect nothing
        elif self._depth == 2 and local == self._entry:
            self._in_entry, self._loc_seen = True, False
        elif self._depth == 3 and self._in_entry and local == "loc" and not self._loc_seen:
            self._loc_seen, self._collecting, self._buf = True, True, []

    def text(self, data: str) -> None:
        if self._collecting and self._depth == 3:
            self._buf.append(data)

    def end(self, _name: str) -> None:
        result = self.result
        if self._depth == 3 and self._collecting:
            self._collecting = False
            value = decode_text("".join(self._buf).encode("utf-8", errors="replace")).strip()
            if not value:
                result.empty_locs += 1
            elif len(result.locs) >= self.max_locs:
                result.truncated = True
            else:
                result.locs.append(value)
        elif self._depth == 2 and self._in_entry:
            self._in_entry = False
            if not self._loc_seen:
                result.missing_locs += 1
        self._depth -= 1


def _refuse(*_args: object) -> None:
    raise _UnsafeXml


def _refuse_external(*_args: object) -> int:
    raise _UnsafeXml


def _make_parser(collector: _Collector) -> expat.XMLParserType:
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartElementHandler = collector.start
    parser.EndElementHandler = collector.end
    parser.CharacterDataHandler = collector.text
    # Refuse every DTD feature at parser level (independent of encoding tricks).
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse_external
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    return parser


def parse_sitemap(data: bytes, *, max_bytes: int = MAX_SITEMAP_BYTES) -> SitemapParseResult:
    """Extract `<loc>` values from a `<urlset>` or `<sitemapindex>`. Never raises."""
    if len(data) > max_bytes:
        return SitemapParseResult(SitemapParseStatus.TOO_LARGE)
    if not data.strip():
        return SitemapParseResult(SitemapParseStatus.EMPTY)
    # UTF-16 payloads would hide the marker from a byte regex, so also check decoded text.
    if _UNSAFE.search(data) or _UNSAFE_WIDE.search(data.decode("utf-16", errors="ignore")[:4096]):
        return SitemapParseResult(SitemapParseStatus.UNSAFE, detail="doctype_or_entity")
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return SitemapParseResult(SitemapParseStatus.MALFORMED, detail="binary")
    collector = _Collector(MAX_LOCS)
    try:
        _make_parser(collector).Parse(data, True)
    except _UnsafeXml:
        return SitemapParseResult(SitemapParseStatus.UNSAFE, detail="doctype_or_entity")
    except (expat.ExpatError, ValueError, LookupError, RecursionError) as exc:
        # (UnicodeError is a ValueError; LookupError covers unknown encodings)
        return SitemapParseResult(SitemapParseStatus.MALFORMED, detail=type(exc).__name__)
    if collector.result.kind is None:
        return SitemapParseResult(
            SitemapParseStatus.UNSUPPORTED, detail=f"root:{(collector.root or '')[:40]}"
        )
    return collector.result
