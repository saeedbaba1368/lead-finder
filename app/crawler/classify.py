"""Phase 7.3.2: deterministic page classification (pure, no network, no AI).

`classify_page(page, url=None)` labels an already parsed page as one of `PageCategory`. It only *describes*
the page: nothing in the crawler reads the result, so crawling is unchanged and no page is ever excluded.

Evidence (integer weights, summed per category; the best category wins if it reaches `MIN_SCORE`):

- URL path (after percent-decoding, so Persian slugs work): keyword in the last segment 4, in an earlier
  segment 3. Blog sections (`/blog`, `/news`, `/articles`, `/وبلاگ` ...) give `blog` when they end the path and
  `article` when a slug follows; a `/YYYY/MM/` date path also means `article`.
- Title: first part (before `|`, ` - `, `:` ...) 2, other parts 1 (site names are usually there).
- Headings: `h1` 2, other headings 1. Metadata (description, keywords, Open Graph title/description): 1.
- Text: a few body phrases ("add to cart", "our mission", "min read" ...), capped at 2 per category, plus one
  structural rule (contact details and an address on a short page). Navigation labels such as "Contact us" are
  deliberately not used as text evidence because they appear on every page.
- Homepage is decided by URL structure alone: an empty or root path, an optional language prefix (`/fa/`,
  `/en-US/`) and/or `index.*` / `home` / `default.*`, with no query string. Without a URL the canonical or
  Open Graph URL is used; with none, a page is never `homepage`.

Ties are broken by the fixed order in `CATEGORY_PRIORITY`; `ambiguous` flags a close runner-up. English and
Persian keywords are matched together, so the page language does not matter (mixed pages work).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:  # avoid a circular import: html_parser imports this module
    from app.crawler.html_parser import ParsedPage


class PageCategory(StrEnum):
    HOMEPAGE = "homepage"
    ABOUT = "about"
    CONTACT = "contact"
    SERVICES = "services"
    PRODUCT = "product"
    BLOG = "blog"
    ARTICLE = "article"
    PORTFOLIO = "portfolio"
    CAREERS = "careers"
    OTHER = "other"


C = PageCategory
MIN_SCORE = 2  # a single weak mention is not enough
HOMEPAGE_SCORE = 10
AMBIGUITY_MARGIN = 1  # runner-up within this many points (and itself >= MIN_SCORE) marks the result ambiguous
CATEGORY_PRIORITY = (C.CAREERS, C.CONTACT, C.ABOUT, C.PORTFOLIO, C.SERVICES, C.PRODUCT, C.ARTICLE, C.BLOG)  # tie-break
MAX_TEXT_CHARS = 50_000

# Keywords for URL, title, heading and metadata evidence. Phrases are matched as whole words after
# normalisation (see `normalize`), so list the common spellings; Persian "ها" plurals are attached to the
# previous word by the normaliser ("نمونه کار ها" == "نمونه‌کارها").
KEYWORDS: dict[PageCategory, tuple[str, ...]] = {
    C.ABOUT: ("about", "about us", "who we are", "our story", "our team", "meet the team", "team", "company profile",
              "درباره ما", "درباره", "درباره شرکت", "معرفی شرکت", "معرفی ما", "تیم ما", "داستان ما"),
    C.CONTACT: ("contact", "contacts", "contact us", "get in touch", "reach us",
                "تماس با ما", "تماس", "ارتباط با ما", "راه های ارتباطی", "راههای ارتباطی"),
    C.SERVICES: ("services", "our services", "what we do", "what we offer", "خدمات", "خدمات ما", "سرویس ها", "سرویسها"),
    C.PRODUCT: ("product", "products", "shop", "store", "catalog", "catalogue",
                "محصول", "محصولات", "فروشگاه", "کاتالوگ"),
    C.BLOG: ("blog", "blogs", "news", "articles", "posts", "magazine", "weblog",
             "وبلاگ", "بلاگ", "اخبار", "مقالات", "مطالب", "مجله"),
    C.PORTFOLIO: ("portfolio", "our work", "case study", "case studies", "projects", "gallery", "showcase",
                  "نمونه کار", "نمونه کارها", "پروژه ها", "پروژهها", "گالری", "نمونه پروژه"),
    C.CAREERS: ("careers", "career", "jobs", "job", "job openings", "join us", "join our team", "work with us",
                "we are hiring", "hiring", "vacancies", "open positions",
                "فرصت شغلی", "فرصت های شغلی", "استخدام", "موقعیت های شغلی", "همکاری با ما", "فرصتهای شغلی"),
}
# A URL segment that names a blog *section*: it is the blog (index) when last, an article when a slug follows.
BLOG_SECTIONS = frozenset(KEYWORDS[C.BLOG])
# A URL segment that names a single article: the page itself is an article.
ARTICLE_SEGMENTS = ("article", "post", "news item", "مقاله", "خبر", "پست")

# Body-text phrases: (phrase, weight, minimum occurrences). The total per category is capped at TEXT_CAP.
TEXT_CAP = 2
TEXT_SIGNALS: dict[PageCategory, tuple[tuple[str, int, int], ...]] = {
    C.ABOUT: (("our mission", 1, 1), ("who we are", 1, 1), ("founded in", 1, 1), ("our story", 1, 1),
              ("ماموریت ما", 1, 1), ("داستان ما", 1, 1)),
    C.CONTACT: (("send us a message", 1, 1), ("send message", 1, 1), ("your message", 1, 1), ("ارسال پیام", 1, 1),
                ("پیام شما", 1, 1)),
    C.SERVICES: (("what we offer", 1, 1), ("our services include", 2, 1)),
    C.PRODUCT: (("add to cart", 2, 1), ("add to basket", 2, 1), ("buy now", 1, 1), ("افزودن به سبد خرید", 2, 1)),
    C.BLOG: (("read more", 1, 3), ("continue reading", 1, 3), ("ادامه مطلب", 1, 3), ("بیشتر بخوانید", 1, 3)),
    C.ARTICLE: (("min read", 1, 1), ("minute read", 1, 1), ("posted on", 1, 1), ("published on", 1, 1),
                ("written by", 1, 1), ("reading time", 1, 1), ("زمان مطالعه", 1, 1), ("منتشر شده در", 1, 1),
                ("نویسنده", 1, 1)),
    C.PORTFOLIO: (("view project", 1, 1), ("view case study", 1, 1), ("مشاهده پروژه", 1, 1)),
    C.CAREERS: (("apply now", 1, 1), ("job description", 1, 1), ("we are hiring", 1, 1), ("ارسال رزومه", 1, 1)),
}
CONTACT_STRUCTURE_MAX_TEXT = 3000
CONTACT_STRUCTURE_SCORE = 2

_DIACRITICS = re.compile("[\u0640\u064b-\u065f\u0670]")  # tatweel and Arabic/Persian harakat
_NON_WORD = re.compile(r"[\W_]+")  # also splits on ZWNJ and punctuation
_PLURAL_HA = re.compile(r"\s(ها|های)(?=\s|$)")
_TITLE_SPLIT = re.compile(r"\s[|\u2013\u2014\u00b7\u2022:\-/»«]\s|[|\u00b7\u2022»«]")
_LANG_SEGMENT = re.compile(r"[a-z]{2}(?:[-_][a-z0-9]{2,4})?")
_ROOT_NAMES = re.compile(r"(?:index|default|home|main)(?:\.[a-z0-9]{2,5})?")
_DATE_PATH = re.compile(r"(?:^|/)(?:19|20)\d{2}/(?:0?[1-9]|1[0-2])(?:/|$)")
_ARABIC_FORMS = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"})  # ي ى ك -> ی ی ک


@dataclass(frozen=True, slots=True)
class PageClassification:
    """Heuristic page type. Descriptive only; it never influences crawling."""

    category: PageCategory = PageCategory.OTHER
    score: int = 0  # evidence weight of the winning category (HOMEPAGE_SCORE for homepages)
    signals: tuple[str, ...] = ()  # which evidence sources backed it: url, title, title_suffix, h1, heading, meta, text, structure
    ambiguous: bool = False  # a different category came within AMBIGUITY_MARGIN points

    def to_dict(self) -> dict[str, object]:
        return {"category": self.category.value, "score": self.score, "signals": list(self.signals),
                "ambiguous": self.ambiguous}


UNCLASSIFIED = PageClassification()


# ----------------------------------------------------------------------------- normalisation
def normalize(value: str) -> str:
    """Lower-case, percent-decoded-by-caller text reduced to space-separated words (Persian-aware)."""
    value = _DIACRITICS.sub("", value.translate(_ARABIC_FORMS).lower())
    value = " ".join(_NON_WORD.sub(" ", value).split())
    return _PLURAL_HA.sub(r"\1", value)


def _compile(phrases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(f" {n} " for p in phrases if (n := normalize(p))))


_KEYWORDS = {cat: _compile(words) for cat, words in KEYWORDS.items()}
_BLOG_SECTIONS = _compile(tuple(BLOG_SECTIONS))
_ARTICLE_SEGMENTS = _compile(ARTICLE_SEGMENTS)
_TEXT_SIGNALS = {
    cat: tuple((f" {normalize(p)} ", w, n) for p, w, n in items) for cat, items in TEXT_SIGNALS.items()
}


def _matches(padded: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in padded for phrase in phrases)


def _categories_in(text: str) -> set[PageCategory]:
    """Categories whose keywords appear in `text` (normalised and padded internally)."""
    padded = f" {normalize(text)} "
    return {cat for cat, phrases in _KEYWORDS.items() if _matches(padded, phrases)}


# ----------------------------------------------------------------------------- URL
def _path_segments(url: str | None) -> tuple[list[str], bool] | None:
    """(decoded non-empty path segments, has_query) or None when there is no usable URL."""
    if not url or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    segments = [seg for s in parts.path.split("/") if (seg := unquote(s).strip())]
    return segments, bool(parts.query)


def is_homepage_path(segments: list[str], has_query: bool) -> bool:
    if has_query:  # "/?p=12" and "/?s=term" are not the homepage
        return False
    if segments and _LANG_SEGMENT.fullmatch(segments[0].lower()):
        segments = segments[1:]
    if not segments:
        return True
    return len(segments) == 1 and _ROOT_NAMES.fullmatch(segments[0].lower()) is not None


def _url_scores(segments: list[str]) -> dict[PageCategory, int]:
    scores: dict[PageCategory, int] = {}

    def add(category: PageCategory, weight: int) -> None:
        scores[category] = max(scores.get(category, 0), weight)

    for index, segment in enumerate(segments):
        weight = 4 if index == len(segments) - 1 else 3
        padded = f" {normalize(segment)} "
        if _matches(padded, _BLOG_SECTIONS):
            add(C.BLOG if index == len(segments) - 1 else C.ARTICLE, 4)
        if _matches(padded, _ARTICLE_SEGMENTS):
            add(C.ARTICLE, weight)
        for category, phrases in _KEYWORDS.items():
            if category is not C.BLOG and _matches(padded, phrases):
                add(category, weight)
    joined = "/".join(segments)
    if _DATE_PATH.search(joined):
        add(C.ARTICLE if len(segments) >= 3 else C.BLOG, 3)
    return scores


# ----------------------------------------------------------------------------- page evidence
def _add(totals: dict[PageCategory, int], hits: dict[PageCategory, set[str]], categories, weight: int, source: str) -> None:
    for category in categories:
        totals[category] = totals.get(category, 0) + weight
        hits.setdefault(category, set()).add(source)


def _text_scores(text: str) -> dict[PageCategory, int]:
    padded = f" {normalize(text[:MAX_TEXT_CHARS])} "
    scores: dict[PageCategory, int] = {}
    for category, items in _TEXT_SIGNALS.items():
        total = sum(weight for phrase, weight, minimum in items if padded.count(phrase) >= minimum)
        if total:
            scores[category] = min(total, TEXT_CAP)
    return scores


def classify_page(page: ParsedPage, url: str | None = None) -> PageClassification:
    """Classify `page`. `url` is the page URL (the final URL after redirects, if known); without it the
    canonical / Open Graph URL is used for URL evidence."""
    parsed_url = _path_segments(url) or _path_segments(page.metadata.canonical_url) or _path_segments(page.metadata.og_url)
    segments: list[str] = []
    if parsed_url is not None:
        segments, has_query = parsed_url
        if is_homepage_path(segments, has_query):
            return PageClassification(C.HOMEPAGE, HOMEPAGE_SCORE, ("url",))

    totals: dict[PageCategory, int] = {}
    hits: dict[PageCategory, set[str]] = {}
    for category, weight in _url_scores(segments).items():
        _add(totals, hits, [category], weight, "url")
    if page.title:
        first, *rest = _TITLE_SPLIT.split(page.title)
        _add(totals, hits, _categories_in(first), 2, "title")
        _add(totals, hits, set().union(*(_categories_in(part) for part in rest)) if rest else (), 1, "title_suffix")
    h1 = " | ".join(h.text for h in page.headings if h.level == 1)
    others = " | ".join(h.text for h in page.headings if h.level != 1)
    _add(totals, hits, _categories_in(h1), 2, "h1")
    _add(totals, hits, _categories_in(others), 1, "heading")
    meta = page.metadata
    meta_text = " | ".join(v for v in (meta.description, meta.keywords, meta.og_title, meta.og_description) if v)
    _add(totals, hits, _categories_in(meta_text), 1, "meta")
    for category, weight in _text_scores(page.text).items():
        _add(totals, hits, [category], weight, "text")
    if (page.emails or page.phones) and page.addresses and len(page.text) <= CONTACT_STRUCTURE_MAX_TEXT:
        _add(totals, hits, [C.CONTACT], CONTACT_STRUCTURE_SCORE, "structure")

    ranked = sorted(
        (c for c in totals if totals[c] >= MIN_SCORE),
        key=lambda c: (-totals[c], CATEGORY_PRIORITY.index(c)),
    )
    if not ranked:
        return UNCLASSIFIED
    best = ranked[0]
    ambiguous = len(ranked) > 1 and totals[best] - totals[ranked[1]] <= AMBIGUITY_MARGIN
    order = ("url", "title", "title_suffix", "h1", "heading", "meta", "text", "structure")
    signals = tuple(s for s in order if s in hits[best])
    return PageClassification(best, totals[best], signals, ambiguous)
