"""Phase 7.3.1: content language detection (pure parsing, no network, no external service).

Order of evidence:

1. Declared metadata, when it yields a usable language: `<html lang>` / `xml:lang`, then `<meta
   http-equiv="content-language">`, `<meta name="language">`, `<meta name="dc.language">`,
   `<meta property="og:locale">`. The first usable value wins. Tags are reduced to the primary language
   subtag (`fa-IR` -> `fa`, `en_US` -> `en`; ISO 639-2 `fas`/`per`/`eng` are mapped to `fa`/`en`). A list such
   as `"fa, en"` that names both Persian and English is `mixed`. Invalid values (`x-default`, `*`, `und`, junk)
   are ignored.
2. Only when nothing usable is declared: script statistics of the visible text. Persian-script letters versus
   Latin letters give `fa`, `en` or `mixed`. Arabic-script text without any Persian evidence (Persian-only
   letters or common Persian function words) is not counted as Persian. Too little text, or text dominated by
   other scripts, gives `unknown`.

`mixed` always means Persian + English. Declared languages other than those are returned as their code (`de`)
without verification. Declared metadata is trusted over content, so a page declaring `lang="en"` stays `en`
even if its text is Persian.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LANG_ENGLISH = "en"
LANG_PERSIAN = "fa"
LANG_MIXED = "mixed"
LANG_UNKNOWN = "unknown"

SOURCE_HTML_LANG = "html_lang"
SOURCE_META = "meta"
SOURCE_CONTENT = "content"
SOURCE_NONE = "none"

MIN_LETTERS = 5  # fewer letters than this: no content-based guess
MIN_MINORITY_LETTERS = 10  # a second language needs at least this many letters to make the page `mixed`
MIXED_MINORITY_SHARE = 0.2  # ... and at least this share of the Persian + Latin letters
KNOWN_SCRIPT_SHARE = 0.5  # Persian + Latin letters must be at least this share of all letters
MAX_ANALYSED_CHARS = 200_000  # long pages are judged on their first part

_ISO_639_2 = {"fas": "fa", "per": "fa", "eng": "en"}
_LANG_TAG = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*")
_NOT_LANGUAGES = frozenset({"und", "mul", "mis", "zxx"})  # ISO 639 "undetermined/multiple/..." codes
_URL_OR_EMAIL = re.compile(r"https?://\S+|www\.\S+|\S+@\S+", re.IGNORECASE)

# Letters that exist in Persian but not in Arabic (and vice versa for the legacy Arabic forms).
_PERSIAN_ONLY_LETTERS = frozenset("\u067e\u0686\u0698\u06af\u06a9\u06cc")  # پ چ ژ گ ک ی
_ARABIC_TO_PERSIAN_FORMS = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"})  # ي ى ك
_PERSIAN_WORDS = frozenset({"است", "این", "را", "برای", "های", "می", "با", "که", "از", "در", "به", "هم", "نیز"})
_ARABIC_SCRIPT_WORD = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]+")


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    """Result of language detection for one page."""

    language: str = LANG_UNKNOWN  # "en", "fa", "mixed", "unknown", or another declared primary code
    source: str = SOURCE_NONE  # "html_lang", "meta", "content" or "none"
    declared: str | None = None  # the raw declared value that was used (whitespace-stripped), if any

    def to_dict(self) -> dict[str, object]:
        return {"language": self.language, "source": self.source, "declared": self.declared}


UNKNOWN = LanguageDetection()


# ----------------------------------------------------------------------------- declared metadata
def normalize_language_tag(value: str | None) -> str | None:
    """Primary language code of a BCP 47 / ISO 639 tag, or None if `value` is not a usable language."""
    if not value:
        return None
    tag = value.strip()
    if _LANG_TAG.fullmatch(tag) is None:
        return None
    primary = tag.replace("_", "-").split("-", 1)[0].lower()
    primary = _ISO_639_2.get(primary, primary)
    return None if primary in _NOT_LANGUAGES else primary


def language_from_declaration(value: str | None) -> str | None:
    """Language for one declared value, which may be a comma/semicolon separated list; None if unusable."""
    if not value or len(value) > 200:
        return None
    codes = [code for part in value.replace(";", ",").split(",") if (code := normalize_language_tag(part))]
    if not codes:
        return None
    if LANG_PERSIAN in codes and LANG_ENGLISH in codes:
        return LANG_MIXED
    return codes[0]


def detect_declared(html_lang: str | None, meta_languages: list[str]) -> LanguageDetection | None:
    """The first usable declaration: `html_lang` first, then `meta_languages` in priority order."""
    if (language := language_from_declaration(html_lang)) is not None:
        return LanguageDetection(language, SOURCE_HTML_LANG, html_lang.strip())  # type: ignore[union-attr]
    for value in meta_languages:
        if (language := language_from_declaration(value)) is not None:
            return LanguageDetection(language, SOURCE_META, value.strip())
    return None


# ----------------------------------------------------------------------------- content
def _is_arabic_script(code: int) -> bool:
    return 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0x08A0 <= code <= 0x08FF or 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF


def _is_latin(code: int) -> bool:
    return 0x41 <= code <= 0x5A or 0x61 <= code <= 0x7A or 0xC0 <= code <= 0x24F or 0x1E00 <= code <= 0x1EFF


def _has_persian_evidence(text: str) -> bool:
    if any(ch in _PERSIAN_ONLY_LETTERS for ch in text):
        return True
    words = _ARABIC_SCRIPT_WORD.findall(text.translate(_ARABIC_TO_PERSIAN_FORMS))
    return any(word in _PERSIAN_WORDS for word in words)


def detect_from_content(text: str) -> str:
    """Language of `text` from script statistics: "en", "fa", "mixed" or "unknown"."""
    text = _URL_OR_EMAIL.sub(" ", text[:MAX_ANALYSED_CHARS])
    arabic = latin = other = 0
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        if _is_arabic_script(code):
            arabic += 1
        elif _is_latin(code):
            latin += 1
        else:
            other += 1
    persian = arabic if arabic and _has_persian_evidence(text) else 0
    if not persian:
        other += arabic
    known = persian + latin
    total = known + other
    if total < MIN_LETTERS or known == 0 or known / total < KNOWN_SCRIPT_SHARE:
        return LANG_UNKNOWN
    persian_share = persian / known
    minority = min(persian, latin)
    if minority >= MIN_MINORITY_LETTERS and minority / known >= MIXED_MINORITY_SHARE:
        return LANG_MIXED
    return LANG_PERSIAN if persian_share >= 0.5 else LANG_ENGLISH


def detect_language(
    text: str, *, html_lang: str | None = None, meta_languages: list[str] | None = None
) -> LanguageDetection:
    """Declared metadata when usable, otherwise content statistics; never raises for text input."""
    if (declared := detect_declared(html_lang, meta_languages or [])) is not None:
        return declared
    language = detect_from_content(text)
    if language == LANG_UNKNOWN:
        return UNKNOWN
    return LanguageDetection(language, SOURCE_CONTENT)
