"""HTML content parsing (Phase 7.1). Pure parsing: no network, no URL normalisation, no URL policy.

`parse_html(html)` turns an HTML string into a `ParsedPage` (title, visible text, headings, raw links,
standard metadata). `parse_response(result)` is the thin bridge from a Phase 6.1 `PageResult`: it decides
whether the response is HTML, decodes the body (HTTP charset, BOM, `<meta charset>`, safe fallback) and
parses it. Built on the standard library `html.parser`, like `links.py`, so there is no new dependency.

Links are returned exactly as written in `<a href>` (stripped, de-duplicated by exact text, document
order). Resolving, normalising and scoping them stays the job of the URL engine (`UrlGate`); the crawler
still discovers links through `extract_links`, unchanged.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from urllib.parse import unquote

from app.core.logging import get_logger
from app.crawler.classify import UNCLASSIFIED as _UNCLASSIFIED
from app.crawler.classify import PageClassification, classify_page
from app.crawler.language import UNKNOWN as _UNKNOWN_LANGUAGE
from app.crawler.language import LanguageDetection, detect_language
from app.crawler.links import MAX_LINKS_PER_PAGE
from app.crawler.metrics import EMPTY_METRICS as _EMPTY_METRICS
from app.crawler.metrics import ContentMetrics, compute_metrics
from app.crawler.models import PageResult
from app.crawler.social import SocialLink, classify_social_url, dedupe_social_links
from app.crawler.structured import (
    MAX_JSONLD_BLOCKS, Address, StructuredBusiness, parse_jsonld, unique_addresses,
)
from app.urls.content import HTML_MEDIA_TYPES

logger = get_logger(__name__)

_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template"})  # never visible text
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Elements that start/end a new line of text (everything else is inline: `<b>he</b>llo` stays "hello").
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "dd", "details", "dialog", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "header", "hr", "li", "main", "nav",
        "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
    | _HEADING_TAGS
)
_META_NAMES = {  # lower-case meta name/property -> PageMetadata field
    "description": "description",
    "keywords": "keywords",
    "robots": "robots",
    "og:title": "og_title",
    "og:description": "og_description",
    "og:url": "og_url",
    "og:site_name": "og_site_name",  # Phase 8.2: the site/brand name, used for business identity
    "og:image": "og_image",  # Phase 12.1: website metadata enrichment
    "twitter:card": "twitter_card",
    "twitter:title": "twitter_title",
    "twitter:description": "twitter_description",
    "twitter:image": "twitter_image",
}
# Phase 7.3.1: declared-language meta tags, in priority order (key = lower-case http-equiv / name / property).
_LANGUAGE_META_KEYS = ("content-language", "language", "dc.language", "dcterms.language", "og:locale")
_META_CHARSET = re.compile(rb"<meta[^>]*?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:\-]+)", re.IGNORECASE)
_SNIFF_BYTES = 4096  # how far into the document `<meta charset>` is looked for
_BINARY_PROBE = 2048  # bytes checked for NUL when the response declares no content type

# Phase 7.2.1 email extraction. Deliberately conservative: a practical subset of RFC 5322 that avoids
# obvious false positives (image names such as logo@2x.png, "a@b", consecutive dots, bad labels).
_EMAIL_LOCAL = r"[A-Za-z0-9](?:[A-Za-z0-9._%+\-]*[A-Za-z0-9])?"
_EMAIL_DOMAIN = r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}"
_EMAIL_RE = re.compile(rf"(?<![A-Za-z0-9._%+\-@])({_EMAIL_LOCAL}@{_EMAIL_DOMAIN})(?![A-Za-z0-9@\-])")
_FILE_LIKE_TLDS = frozenset(  # "logo@2x.png"-style asset names are not mailboxes
    {"png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "bmp", "css", "js", "woff", "woff2", "ttf", "eot"}
)
_MAX_EMAILS_PER_PAGE = 500

# Phase 7.2.2 phone extraction. Conservative by design: a number must look like a phone number (a leading
# "+"/"00", separators, a leading national "0", or a "tel"-style label) and must not look like a date, price,
# ID, postal code, IP address or card number. No country is assumed, so local numbers are never converted to
# international form and "021 1234 5678" and "+98 21 1234 5678" stay two different values.
_PHONE_CANDIDATE = re.compile(
    r"(?<![\w+@/])(?<!\d[,.\-/])"  # not inside a word, an address, a path, or a longer digit run
    r"(\+ ?)?(\(?\d+\)?(?:[ .\-\u2010-\u2015]\(?\d+\)?)*)"
    r"(?![\w@])(?![.\-/,:]\d)"
)
_PHONE_SEPARATORS = re.compile(r"[ .\-\u2010-\u2015()]")
_PHONE_REJECT_BEFORE = re.compile(  # labels that make the following number an ID/price, not a phone
    r"(?:#|(?<!\w)(?:id|ref|sku|isbn|zip|postcode|postal(?: code)?|order|invoice|tracking|account|price|cost|total"
    r"|amount|کد\s*پستی|کد\s*ملی|شناسه|سفارش|فاکتور|قیمت|هزینه|مبلغ))\W{0,3}$|[$€£¥₹₽₺﷼]\s*$",
    re.IGNORECASE,
)
_PHONE_REJECT_AFTER = re.compile(  # currency / percent / unit right after the number
    r"\s*(?:[$€£¥₹₽₺﷼%]|(?:usd|eur|gbp|irr|irt|rials?|tomans?|dollars?|euros?|تومان|ریال|دلار|یورو|درصد|kg|km|mb|gb|mhz|ghz)(?![a-z]))",
    re.IGNORECASE,
)
_PHONE_LABEL_BEFORE = re.compile(  # "Tel:", "Phone", "تلفن:" ... allow a plain unseparated number
    r"(?<!\w)(?:tel|telephone|phone|mobile|cell|fax|call|whatsapp|تلفن|تماس|موبایل|همراه|فکس|واتساپ)\W{0,4}$",
    re.IGNORECASE,
)
_NOT_PHONE_SHAPES = tuple(
    re.compile(pattern)
    for pattern in (
        r"\d+\.\d+",  # decimal / price
        r"\d{1,3}(?:\.\d{1,3}){3}",  # IPv4, dotted versions
        r"(?:1[34]|19|20)\d{2}[-. \u2013]\d{1,2}[-. \u2013]\d{1,2}",  # Y-M-D (Gregorian and Persian years)
        r"\d{1,2}[-. ]\d{1,2}[-. ](?:1[34]|19|20)\d{2}",  # D-M-Y
        r"(?:1[34]|19|20)\d{2}[-\u2013 ](?:1[34]|19|20)\d{2}",  # year range
        r"\d{5}-\d{4}",  # US ZIP+4
        r"\d{5}-\d{5}",  # Iranian postal code written with a hyphen
        r"\d{3}-\d{2}-\d{4}",  # SSN-like
        r"\d{4}(?:[- ]\d{4}){2,3}",  # card-like / repeated 4-digit groups
    )
)
_MAX_PHONES_PER_PAGE = 500

# Phase 7.2.4 structured data. `<address>` text is kept only up to this size (longer is a misused wrapper).
_MAX_ADDRESS_CHARS = 500
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
_ADDRESS_PROPS = {  # microdata itemprop (lower-case) -> Address field
    "streetaddress": "street", "addresslocality": "city", "addressregion": "region",
    "postalcode": "postal_code", "addresscountry": "country",
}


# ----------------------------------------------------------------------------- model
@dataclass(frozen=True, slots=True)
class Heading:
    level: int  # 1..6
    text: str  # whitespace-normalised, never empty


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """A phone number found on a page (Phase 7.2.2)."""

    number: str  # normalised: "+" and digits for international numbers, digits only (leading 0 kept) for local ones
    raw: str  # as written on the page (whitespace-normalised, original digit script kept) or the `tel:` value


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """Standard metadata. A missing (or blank) value is None."""

    description: str | None = None
    keywords: str | None = None
    canonical_url: str | None = None  # raw href of <link rel="canonical">, not resolved or normalised
    robots: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_url: str | None = None
    og_site_name: str | None = None  # Phase 8.2
    # Phase 12.1: raw values (image/favicon hrefs are not resolved or normalised here; see app/leads/metadata.py)
    og_image: str | None = None
    favicon_url: str | None = None  # raw href of the first <link rel="icon"> / <link rel="shortcut icon">
    twitter_card: str | None = None
    twitter_title: str | None = None
    twitter_description: str | None = None
    twitter_image: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedPage:
    title: str | None = None  # None when missing or blank
    text: str = ""  # visible text, one line per block, whitespace-normalised
    headings: tuple[Heading, ...] = ()  # h1..h6 in document order
    links: tuple[str, ...] = ()  # raw <a href> values, document order
    metadata: PageMetadata = field(default_factory=PageMetadata)
    emails: tuple[str, ...] = ()  # Phase 7.2.1: lower-case, de-duplicated, sorted
    phones: tuple[PhoneNumber, ...] = ()  # Phase 7.2.2: de-duplicated by `number`, sorted by `number`
    social_links: tuple[SocialLink, ...] = ()  # Phase 7.2.3: canonical profile URLs, unique, sorted by platform/url
    addresses: tuple[Address, ...] = ()  # Phase 7.2.4: schema.org + <address> addresses, unique, sorted
    structured_data: tuple[StructuredBusiness, ...] = ()  # Phase 7.2.4: JSON-LD business/person records, sorted
    language: LanguageDetection = _UNKNOWN_LANGUAGE  # Phase 7.3.1: en / fa / mixed / unknown (or a declared code)
    encoding: str | None = None  # Phase 7.3.1: codec used to decode the body; None when parsed from a str
    classification: PageClassification = _UNCLASSIFIED  # Phase 7.3.2: heuristic page type (descriptive only)
    metrics: ContentMetrics = _EMPTY_METRICS  # Phase 7.3.3: counts and text/HTML ratio (descriptive only)

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "text": self.text,
            "headings": [{"level": h.level, "text": h.text} for h in self.headings],
            "links": list(self.links),
            "metadata": {
                "description": self.metadata.description,
                "keywords": self.metadata.keywords,
                "canonical_url": self.metadata.canonical_url,
                "robots": self.metadata.robots,
                "og_title": self.metadata.og_title,
                "og_description": self.metadata.og_description,
                "og_url": self.metadata.og_url,
                "og_site_name": self.metadata.og_site_name,
                "og_image": self.metadata.og_image,
                "favicon_url": self.metadata.favicon_url,
                "twitter_card": self.metadata.twitter_card,
                "twitter_title": self.metadata.twitter_title,
                "twitter_description": self.metadata.twitter_description,
                "twitter_image": self.metadata.twitter_image,
            },
            "emails": list(self.emails),
            "phones": [{"number": p.number, "raw": p.raw} for p in self.phones],
            "social_links": [{"platform": link.platform, "url": link.url} for link in self.social_links],
            "addresses": [address.to_dict() for address in self.addresses],
            "structured_data": [entity.to_dict() for entity in self.structured_data],
            "language": self.language.to_dict(),
            "encoding": self.encoding,
            "classification": self.classification.to_dict(),
            "metrics": self.metrics.to_dict(),
        }


# ----------------------------------------------------------------------------- helpers
def _clean(value: str | None) -> str | None:
    """Collapse whitespace; blank -> None. Only whitespace changes (ZWNJ and other characters are kept)."""
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _valid_email(candidate: str) -> str | None:
    """Return the lower-cased address if `candidate` passes the false-positive checks, else None."""
    local, _, domain = candidate.rpartition("@")
    if not local or ".." in candidate or len(local) > 64 or len(candidate) > 254:
        return None
    if domain.rsplit(".", 1)[-1].lower() in _FILE_LIKE_TLDS:
        return None
    return candidate.lower()


def extract_emails_from_text(text: str) -> list[str]:
    """Valid addresses found in plain text, in order of appearance (not de-duplicated)."""
    found = []
    for match in _EMAIL_RE.finditer(text):
        if (email := _valid_email(match.group(1))) is not None:
            found.append(email)
    return found


def _email_from_mailto(href: str) -> list[str]:
    """Addresses in a `mailto:` href (several may be comma-separated; `?subject=...` is ignored)."""
    href = href.strip()
    if href[:7].lower() != "mailto:":
        return []
    recipients = unquote(href[7:].split("?", 1)[0])
    found = []
    for part in recipients.replace(";", ",").split(","):
        part = " ".join(part.split())
        if (match := _EMAIL_RE.fullmatch(part)) is not None and (email := _valid_email(match.group(1))) is not None:
            found.append(email)
    return found


def _ascii_digits(value: str) -> str:
    """Persian (۰-۹), Arabic-Indic (٠-٩) and other Unicode decimal digits -> ASCII."""
    return re.sub(r"\d", lambda m: str(unicodedata.digit(m.group())), value)


def _phone_number(raw: str, *, explicit: bool, labelled: bool = False) -> str | None:
    """Normalise `raw` and decide whether it is a plausible phone number; None means no.

    `explicit` is True for `tel:` links (the page said it is a phone, so only the length is checked).
    `labelled` is True when visible text puts a "Tel:"-style label right before the number.
    """
    value = _ascii_digits(" ".join(raw.split()))
    digits = re.sub(r"\D", "", value)
    if not digits or len(set(digits)) == 1:
        return None
    international = value.startswith("+") or value.startswith("00")
    if international:
        value = value.replace("(0)", "")  # "+49 (0) 30 ..." : the trunk 0 is not dialled internationally
        digits = re.sub(r"\D", "", value)
        national = digits[2:] if value.startswith("00") else digits
        if not 7 <= len(national) <= 15 or national.startswith("0"):
            return None
        return "+" + national
    if not (5 if explicit else 7) <= len(digits) <= (15 if explicit else 12):
        return None
    if not explicit:
        separated = _PHONE_SEPARATORS.search(value) is not None
        if not separated and not labelled and not (digits.startswith("0") and len(digits) >= 10):
            return None  # a bare digit run is far more often an ID than a phone number
        if any(shape.fullmatch(value) for shape in _NOT_PHONE_SHAPES):
            return None
        if sum(len(group) == 1 for group in re.findall(r"\d+", value)) >= 2:
            return None  # "1 2 3 4 5 6 7", "1.2.3.4567"; a real phone has at most a leading "1"
        if digits[:3] in ("978", "979") and len(digits) == 13:
            return None  # ISBN-13
    return digits


def _phone_from_tel(href: str) -> PhoneNumber | None:
    """The number in a `tel:` href (parameters such as `;ext=1` are dropped), or None."""
    href = href.strip()
    if href[:4].lower() != "tel:":
        return None
    raw = " ".join(unquote(href[4:].split(";", 1)[0].split("?", 1)[0]).split())
    if re.fullmatch(r"\+?[\d ().\-\u2010-\u2015]+", raw) is None:
        return None  # letters ("1-800-FLOWERS"), empty, or not a number
    number = _phone_number(raw, explicit=True)
    return PhoneNumber(number, raw) if number is not None else None


def extract_phones_from_text(text: str) -> list[PhoneNumber]:
    """Plausible phone numbers in plain text, in order of appearance (not de-duplicated)."""
    found = []
    for match in _PHONE_CANDIDATE.finditer(text):
        raw = match.group(0).strip()
        before = text[max(0, match.start() - 30) : match.start()].rsplit("\n", 1)[-1]
        after = text[match.end() : match.end() + 20].split("\n", 1)[0]
        if _PHONE_REJECT_BEFORE.search(before) or _PHONE_REJECT_AFTER.match(after):
            continue
        number = _phone_number(raw, explicit=False, labelled=_PHONE_LABEL_BEFORE.search(before) is not None)
        if number is not None:
            found.append(PhoneNumber(number, raw))
    return found


def _is_jsonld_script(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        if name.lower() == "type":
            return (value or "").split(";", 1)[0].strip().lower() == "application/ld+json"
    return False


def is_html_content_type(content_type: str | None) -> bool:
    """True for text/html and application/xhtml+xml (parameters and case are ignored). None -> False."""
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() in HTML_MEDIA_TYPES


def decode_html_with_encoding(body: bytes, charset: str | None = None) -> tuple[str, str]:
    """Like `decode_html`, but also returns the codec name that was used (`codecs.lookup(...).name`).

    A BOM gives `utf-8` / `utf-16`; when nothing decodes cleanly the result is UTF-8 with replacement
    characters and the encoding reported is `utf-8`.
    """
    if body.startswith(codecs.BOM_UTF8):
        return body[3:].decode("utf-8", errors="replace"), "utf-8"
    if body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return body.decode("utf-16", errors="replace"), "utf-16"
    candidates: list[str] = []
    if charset:
        candidates.append(charset.strip().strip("\"'"))
    if (match := _META_CHARSET.search(body[:_SNIFF_BYTES])) is not None:
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    candidates.append("utf-8")
    for label in candidates:
        try:
            name = codecs.lookup(label).name
            return body.decode(label), name
        except (LookupError, UnicodeDecodeError, ValueError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8"


def decode_html(body: bytes, charset: str | None = None) -> str:
    """Decode an HTML body without corrupting Unicode.

    Order: byte-order mark, the HTTP charset, a `<meta charset>` in the first bytes, then UTF-8. A candidate
    that is unknown or cannot decode the bytes is skipped; the last resort is UTF-8 with replacement.
    """
    return decode_html_with_encoding(body, charset)[0]


def _classify(page: ParsedPage, url: str | None) -> PageClassification:
    """Phase 7.3.2 classification; a failure is logged and leaves the page unclassified (never raises)."""
    try:
        return classify_page(page, url)
    except Exception as exc:  # noqa: BLE001 - classification must never break page parsing
        logger.warning("page_classify_failed", extra={"error": repr(exc)})
        return _UNCLASSIFIED


# ----------------------------------------------------------------------------- parser
class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skipping: dict[str, int] = {}  # open script/style/noscript/template elements
        self._skip_depth = 0
        self._in_title = False
        self._title_done = False  # the first <title> element has been read; later ones are ignored
        self._title_wanted = True  # the <title> currently open is the first one
        self._title: list[str] = []
        self._text: list[str] = []
        self._heading_level: int | None = None
        self._heading: list[str] = []
        self.headings: list[Heading] = []
        self.links: list[str] = []
        self._seen_links: set[str] = set()
        self.meta: dict[str, str] = {}
        self.mailto_emails: list[str] = []
        self.tel_phones: list[PhoneNumber] = []
        self.social: list[SocialLink] = []
        self._ld_buf: list[str] | None = None  # text of the open <script type="application/ld+json">
        self._ld_blocks: list[str] = []
        self._addr_depth = 0  # open <address> elements (only the outermost one collects)
        self._addr_buf: list[str] = []
        self._addr_props: dict[str, list[str]] = {}
        self._addr_stack: list[tuple[str, str | None]] = []  # open elements inside <address>: (tag, address field)
        self.address_elements: list[Address] = []
        self.html_length = 0  # Phase 7.3.3: characters of the HTML string being parsed (set by parse_html)
        self.image_count = 0
        self.anchor_count = 0  # <a href> elements with a non-blank href, before de-duplication and the link cap
        self.html_lang: str | None = None  # Phase 7.3.1: lang / xml:lang of the first <html> element
        self._html_seen = False
        self._meta_lang: dict[str, str] = {}  # first non-blank value per language meta key

    # -- tag events
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._in_title:  # a tag inside <title> means the title was never closed: stop collecting
            self._end_title()
        if tag in _SKIPPED_TAGS:
            if tag == "script" and self._skip_depth == 0 and self._ld_buf is None and _is_jsonld_script(attrs):
                self._ld_buf = []
            self._skipping[tag] = self._skipping.get(tag, 0) + 1
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attributes = {name.lower(): value for name, value in attrs}
        if tag == "html" and not self._html_seen:
            self._html_seen = True
            self.html_lang = _clean(attributes.get("lang")) or _clean(attributes.get("xml:lang"))
        if tag == "title":
            self._in_title = True
            self._title_wanted = not self._title_done
            return
        if tag == "meta":
            self._meta(attributes)
            self._meta_language(attributes)
        elif tag == "link":
            self._link(attributes)
        elif tag == "a":
            self._anchor(attributes)
        elif tag == "img":
            self.image_count += 1
        if self._addr_depth or tag == "address":
            self._address_start(tag, attributes)
        if tag in _BLOCK_TAGS and tag != "br":
            self._end_heading()  # an unclosed heading ends at the next block-level element
        if tag in _HEADING_TAGS:
            self._heading_level = int(tag[1])
            self._heading = []
        if tag in _BLOCK_TAGS:
            self._text.append("\n")
            if self._addr_depth:
                self._addr_buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            if tag == "script" and self._ld_buf is not None and self._skip_depth == 1:
                self._ld_blocks.append("".join(self._ld_buf))
                self._ld_buf = None
            if self._skipping.get(tag, 0) > 0:
                self._skipping[tag] -= 1
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._end_title()
            return
        if self._addr_depth:
            self._address_end(tag)
        if tag in _HEADING_TAGS:
            self._end_heading()
        if tag in _BLOCK_TAGS:
            self._text.append("\n")
            if self._addr_depth:
                self._addr_buf.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            if self._ld_buf is not None:
                self._ld_buf.append(data)
            return
        if self._in_title:
            if self._title_wanted:
                self._title.append(data)
            return
        self._text.append(data.replace("\n", " "))  # "\n" in _text is reserved for block boundaries
        if self._addr_depth:
            chunk = data.replace("\n", " ")
            self._addr_buf.append(chunk)
            for _, field_name in self._addr_stack:
                if field_name:
                    self._addr_props.setdefault(field_name, []).append(chunk)
        if self._heading_level is not None:
            self._heading.append(data)

    # -- <address> collection (Phase 7.2.4)
    def _address_start(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag == "address":
            if self._addr_depth == 0:
                self._addr_buf, self._addr_props, self._addr_stack = [], {}, []
            self._addr_depth += 1
            return
        field_name = _ADDRESS_PROPS.get((attributes.get("itemprop") or "").strip().lower())
        if tag in _VOID_TAGS:
            if field_name and (content := _clean(attributes.get("content"))):  # <meta itemprop=... content=...>
                self._addr_props.setdefault(field_name, []).append(content)
            return
        self._addr_stack.append((tag, field_name))

    def _address_end(self, tag: str) -> None:
        if tag == "address":
            self._addr_depth -= 1
            if self._addr_depth == 0:
                self._finish_address()
        elif tag not in _VOID_TAGS:
            for index in range(len(self._addr_stack) - 1, -1, -1):
                if self._addr_stack[index][0] == tag:
                    del self._addr_stack[index:]
                    break

    def _finish_address(self) -> None:
        lines = (" ".join(line.split()).rstrip(",\u060c;") for line in "".join(self._addr_buf).split("\n"))
        formatted = ", ".join(line for line in lines if line)
        if formatted and len(formatted) <= _MAX_ADDRESS_CHARS:
            parts = {name: _clean(" ".join(self._addr_props.get(name, []))) for name in _ADDRESS_PROPS.values()}
            self.address_elements.append(Address(formatted=formatted, **parts))
        self._addr_buf, self._addr_props, self._addr_stack = [], {}, []

    # -- helpers
    def _end_title(self) -> None:
        self._in_title = False
        self._title_done = True

    def _end_heading(self) -> None:
        if self._heading_level is None:
            return
        text = _clean("".join(self._heading))
        if text:
            self.headings.append(Heading(self._heading_level, text))
        self._heading_level = None
        self._heading = []

    def _meta(self, attributes: dict[str, str | None]) -> None:
        key = (attributes.get("name") or attributes.get("property") or "").strip().lower()
        field_name = _META_NAMES.get(key)
        if field_name is None or field_name in self.meta:  # first occurrence wins
            return
        if (value := _clean(attributes.get("content"))) is not None:
            self.meta[field_name] = value

    def _meta_language(self, attributes: dict[str, str | None]) -> None:
        key = (attributes.get("http-equiv") or attributes.get("name") or attributes.get("property") or "").strip().lower()
        if key in _LANGUAGE_META_KEYS and key not in self._meta_lang and (value := _clean(attributes.get("content"))):
            self._meta_lang[key] = value

    def _link(self, attributes: dict[str, str | None]) -> None:
        rel = (attributes.get("rel") or "").lower().split()
        if "canonical" in rel and "canonical_url" not in self.meta:
            href = (attributes.get("href") or "").strip()
            if href:
                self.meta["canonical_url"] = href
        if "icon" in rel and "favicon_url" not in self.meta:  # "icon" and "shortcut icon"; first one wins
            href = (attributes.get("href") or "").strip()
            if href:
                self.meta["favicon_url"] = href

    def _anchor(self, attributes: dict[str, str | None]) -> None:
        href = attributes.get("href")
        if href is None:
            return
        if href.strip():
            self.anchor_count += 1
        self.mailto_emails.extend(_email_from_mailto(href))  # independent of the link cap
        if (tel := _phone_from_tel(href)) is not None:
            self.tel_phones.append(tel)
        if (social := classify_social_url(href)) is not None:  # also independent of the link cap
            self.social.append(social)
        if len(self.links) >= MAX_LINKS_PER_PAGE:
            return
        href = href.strip()
        if href and href not in self._seen_links:
            self._seen_links.add(href)
            self.links.append(href)

    def result(self, url: str | None = None) -> ParsedPage:
        self._end_heading()  # an unclosed heading at end of input still counts
        lines = [" ".join(line.split()) for line in "".join(self._text).split("\n")]
        text = "\n".join(line for line in lines if line)
        emails = sorted({*self.mailto_emails, *extract_emails_from_text(text)})[:_MAX_EMAILS_PER_PAGE]
        if self._addr_depth:  # unclosed <address> at end of input still counts
            self._addr_depth = 0
            self._finish_address()
        if self._ld_buf is not None:  # unclosed JSON-LD script: usually malformed, parse_jsonld skips it
            self._ld_blocks.append("".join(self._ld_buf))
            self._ld_buf = None
        try:
            structured = parse_jsonld(self._ld_blocks[:MAX_JSONLD_BLOCKS])
        except Exception as exc:  # noqa: BLE001 - structured data must never break page parsing
            logger.warning("jsonld_parse_failed", extra={"error": repr(exc)})
            structured = ()
        addresses = unique_addresses((*(a for entity in structured for a in entity.addresses), *self.address_elements))
        phones: dict[str, PhoneNumber] = {}
        for phone in (*self.tel_phones, *extract_phones_from_text(text)):  # first seen wins: tel links, then text
            phones.setdefault(phone.number, phone)
        title = _clean("".join(self._title))
        try:
            language = detect_language(
                f"{title or ''}\n{text}",
                html_lang=self.html_lang,
                meta_languages=[self._meta_lang[k] for k in _LANGUAGE_META_KEYS if k in self._meta_lang],
            )
        except Exception as exc:  # noqa: BLE001 - language detection must never break page parsing
            logger.warning("language_detect_failed", extra={"error": repr(exc)})
            language = _UNKNOWN_LANGUAGE
        page = ParsedPage(
            language=language,
            title=title,
            text=text,
            emails=tuple(emails),
            phones=tuple(phones[number] for number in sorted(phones))[:_MAX_PHONES_PER_PAGE],
            social_links=dedupe_social_links(self.social),
            addresses=addresses,
            structured_data=structured,
            headings=tuple(self.headings),
            links=tuple(self.links),
            metadata=PageMetadata(**self.meta),
        )
        try:
            metrics = compute_metrics(
                page, html_length=self.html_length, image_count=self.image_count, link_count=self.anchor_count
            )
        except Exception as exc:  # noqa: BLE001 - metrics must never break page parsing
            logger.warning("page_metrics_failed", extra={"error": repr(exc)})
            metrics = _EMPTY_METRICS
        return replace(page, classification=_classify(page, url), metrics=metrics)


# ----------------------------------------------------------------------------- public API
def parse_html(html: str, url: str | None = None) -> ParsedPage:
    """Parse an HTML document. Malformed markup is tolerated; an empty document gives an empty page.

    `url` (Phase 7.3.4) is only the page address handed to the classifier (URL signals); None classifies
    without URL signals, as before. The analysis stages run once, in this order: contact extraction,
    structured data, language, classification, metrics.

    If `html.parser` itself fails on pathological input, what was collected so far is returned and the
    problem is logged: this function does not raise for markup problems (only `TypeError` for non-str).
    """
    if not isinstance(html, str):
        raise TypeError("parse_html() expects str; use parse_response() or decode_html() for bytes")
    parser = _PageParser()
    parser.html_length = len(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - keep the partial result, never crash the caller
        logger.warning("html_parse_partial", extra={"error": repr(exc)})
    return parser.result(url)


def parse_response(result: PageResult) -> ParsedPage | None:
    """Parse the body of a fetch result. None means "nothing to parse", not an error:

    the fetch was not OK, the body is empty, the content type is declared and not HTML, or (no content
    type declared) the body looks binary.
    """
    if not result.ok or not result.body:
        return None
    if result.content_type:
        if not is_html_content_type(result.content_type):
            return None
    elif b"\x00" in result.body[:_BINARY_PROBE]:  # no declared type: refuse obvious binary content
        return None
    html, encoding = decode_html_with_encoding(result.body, result.charset)
    page = parse_html(html, result.final_url or result.url)  # one pass: classification already uses the URL
    return replace(page, encoding=encoding)
