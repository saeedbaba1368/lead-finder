"""Phase 7.3.1: content language and encoding detection (pure parsing, no network)."""

from __future__ import annotations

import pytest

from app.crawler import LanguageDetection, PageResult, FetchOutcome, ParsedPage, detect_language, parse_html, parse_response
from app.crawler.language import detect_from_content, language_from_declaration, normalize_language_tag

FA = "این یک متن فارسی برای آزمایش تشخیص زبان است و باید درست شناخته شود."
EN = "This is a plain English paragraph used to test language detection on a web page."


def page(body: str, head: str = "", html_attrs: str = "") -> ParsedPage:
    return parse_html(f"<html{html_attrs}><head><title></title>{head}</head><body>{body}</body></html>")


def result(body: bytes, charset: str | None = "utf-8") -> PageResult:
    return PageResult(url="https://e.example/", final_url="https://e.example/", outcome=FetchOutcome.OK,
                      status_code=200, content_type="text/html", charset=charset, body=body, size=len(body))


# 1. html lang=en
def test_html_lang_en():
    p = page(f"<p>{FA}</p>", html_attrs=' lang="en"')  # declaration beats content
    assert p.language == LanguageDetection("en", "html_lang", "en")


def test_html_lang_region_and_case():
    assert page(EN, html_attrs=' lang="EN-us"').language.language == "en"
    assert page(EN, html_attrs=" lang='en_GB'").language.language == "en"


# 2. html lang=fa
def test_html_lang_fa():
    p = page(f"<p>{EN}</p>", html_attrs=' lang="fa"')
    assert p.language == LanguageDetection("fa", "html_lang", "fa")


@pytest.mark.parametrize("tag", ["fa-IR", "fa-AF", "FA", "fas", "per", "fa_IR"])
def test_persian_tag_variants(tag):
    assert normalize_language_tag(tag) == "fa"


def test_xml_lang_used_when_lang_missing():
    assert page(EN, html_attrs=' xml:lang="fa"').language.language == "fa"


def test_meta_language_sources_in_priority_order():
    p = page(EN, head='<meta property="og:locale" content="en_US"><meta http-equiv="Content-Language" content="fa">')
    assert p.language == LanguageDetection("fa", "meta", "fa")
    assert page(EN, head='<meta name="language" content="fa-IR">').language.language == "fa"
    assert page(EN, head='<meta name="DC.language" content="fa">').language.language == "fa"
    assert page(FA, head='<meta property="og:locale" content="en_US">').language.language == "en"


def test_html_lang_beats_meta():
    p = page(FA, head='<meta http-equiv="content-language" content="fa">', html_attrs=' lang="en"')
    assert p.language.language == "en" and p.language.source == "html_lang"


def test_declared_list_with_both_is_mixed():
    assert page(EN, head='<meta http-equiv="content-language" content="fa, en">').language.language == "mixed"
    assert language_from_declaration("en, de") == "en"


@pytest.mark.parametrize("bad", ["", "  ", "x-default", "*", "und", "12", "english language", "!!"])
def test_invalid_declared_values_fall_back_to_content(bad):
    p = page(f"<p>{FA}</p>", html_attrs=f' lang="{bad}"')
    assert p.language.source == "content" and p.language.language == "fa"


def test_other_declared_language_is_reported_without_verification():
    assert page(EN, html_attrs=' lang="de"').language == LanguageDetection("de", "html_lang", "de")


# 3. Persian content
def test_persian_content():
    p = page(f"<h1>خوش آمدید</h1><p>{FA}</p>")
    assert p.language == LanguageDetection("fa", "content", None)


def test_persian_content_with_digits_and_zwnj():
    assert detect_from_content("می‌خواهم ۱۲۳ کتاب بخرم، چون خوب است") == "fa"


def test_persian_with_few_latin_brand_names_stays_persian():
    assert detect_from_content(FA + " Google Apple") == "fa"


def test_arabic_text_is_not_persian():
    assert detect_from_content("هذا نص عربي بسيط يستخدم لاختبار الكشف عن اللغة في الصفحة") == "unknown"


def test_legacy_arabic_letters_with_persian_words_still_persian():
    assert detect_from_content("اين سايت براي كاربران است كه خدمات مي دهد") == "fa"


# 4. English content
def test_english_content():
    assert page(f"<p>{EN}</p>").language == LanguageDetection("en", "content", None)


def test_english_ignores_urls_and_emails():
    assert detect_from_content(f"{FA[:10]} https://aaaaaaaaaaaaaaaaaaaa.example/path info@bbbbbbbbbbbbbbbbbb.example") != "en"


def test_latin_accents_count_as_latin():
    assert detect_from_content("Café résumé naïve façade über") == "en"


# 5. mixed
def test_mixed_content():
    p = page(f"<p>{FA}</p><p>{EN}</p>")
    assert p.language == LanguageDetection("mixed", "content", None)


def test_mixed_needs_enough_minority_text():
    assert detect_from_content(EN + " سلام") == "en"
    assert detect_from_content(FA + " Hello") == "fa"


def test_mixed_in_either_direction():
    assert detect_from_content(EN + " " + FA[:30]) == "mixed"


# 6. missing language metadata
def test_missing_metadata_uses_content():
    p = page(f"<p>{EN}</p>")
    assert p.language.source == "content" and p.language.declared is None


def test_title_counts_as_content():
    p = parse_html("<title>" + FA + "</title>")
    assert p.language.language == "fa"


# 7. malformed HTML
def test_malformed_html():
    p = parse_html(f"<html lang=fa><body><div><p>{FA}<b>unclosed <i>tags <a href='x")
    assert p.language == LanguageDetection("fa", "html_lang", "fa")


def test_malformed_without_lang_and_unclosed_html_tag():
    p = parse_html(f"<html <body><p>{EN}</p")
    assert isinstance(p.language, LanguageDetection)


def test_lang_attribute_without_value_and_duplicate_html_tags():
    assert parse_html(f"<html lang><body>{FA}").language.source == "content"
    assert parse_html('<html lang="en"><html lang="fa"><body>x').language.language == "en"


def test_scripts_and_styles_do_not_influence_content():
    p = page(f"<script>{'var englishWords = 1;' * 50}</script><style>.cls{{}}</style><p>{FA}</p>")
    assert p.language.language == "fa"


# 8. Unicode
def test_unicode_text_intact():
    text = "سلام دنیا 🌍 Hello café ½ 日本語"
    p = page(f"<p>{text}</p>")
    assert text in p.text
    assert p.language.language in {"mixed", "unknown", "fa", "en"}


def test_other_scripts_are_unknown():
    assert detect_from_content("これは日本語のテキストです。言語検出のテスト。") == "unknown"
    assert detect_from_content("Это русский текст для проверки определения языка") == "unknown"


def test_emoji_and_symbols_only_unknown():
    assert detect_from_content("😀😀😀 ★★★ 12345 !!!") == "unknown"


def test_zwnj_and_entities_preserved():
    p = page("<p>می&zwnj;خواهم &amp; کتاب بخرم &copy;</p>")
    assert "می\u200cخواهم & کتاب بخرم ©" in p.text


def test_encoding_utf8_roundtrip():
    body = f"<html><body><p>{FA}</p></body></html>".encode("utf-8")
    p = parse_response(result(body))
    assert p.encoding == "utf-8" and FA in p.text and p.language.language == "fa"


def test_encoding_bom_meta_and_legacy():
    assert parse_response(result(b"\xef\xbb\xbf" + "<p>سلام</p>".encode("utf-8"), None)).encoding == "utf-8"
    assert parse_response(result("<p>سلام</p>".encode("utf-16"), None)).encoding == "utf-16"
    legacy = FA.replace("\u06cc", "\u064a")  # windows-1256 has no Persian yeh; legacy pages use Arabic yeh
    body = f'<meta charset="windows-1256"><p>{legacy}</p>'.encode("cp1256")
    p = parse_response(result(body, None))
    assert p.encoding == "cp1256" and legacy in p.text and p.language.language == "fa"


def test_encoding_unknown_label_and_invalid_bytes():
    assert parse_response(result(b"<p>hello</p>", "no-such-charset")).encoding == "utf-8"
    p = parse_response(result(b"<p>ok \xff\xfe bad</p>", None))
    assert p.encoding == "utf-8" and "\ufffd" in p.text


def test_encoding_none_when_parsed_from_str():
    assert parse_html("<p>x</p>").encoding is None


# 9. empty page
@pytest.mark.parametrize("html", ["", "   \n ", "<html></html>", "<html lang=''><body></body></html>"])
def test_empty_page_is_unknown(html):
    assert parse_html(html).language == LanguageDetection("unknown", "none", None)


def test_too_little_text_is_unknown():
    assert detect_from_content("Hi") == "unknown"
    assert parse_html("<p>OK</p>").language.language == "unknown"


def test_default_parsed_page_language_is_unknown():
    assert ParsedPage().language == LanguageDetection() and ParsedPage().encoding is None


# model / API
def test_to_dict_contains_language_and_encoding():
    d = parse_html(f"<html lang='fa'><body>{FA}").to_dict()
    assert d["language"] == {"language": "fa", "source": "html_lang", "declared": "fa"}
    assert d["encoding"] is None


def test_detect_language_function_directly():
    assert detect_language(EN) == LanguageDetection("en", "content")
    assert detect_language("", html_lang="fa") == LanguageDetection("fa", "html_lang", "fa")
    assert detect_language(EN, meta_languages=["", "fa"]).source == "meta"


def test_very_long_text_is_fast_and_bounded():
    assert detect_from_content("hello world " * 100_000) == "en"


def test_existing_fields_unchanged_by_language_detection():
    p = page(f"<h1>Title</h1><p>{EN} a@b.example</p>", html_attrs=' lang="fa"')
    assert p.headings[0].text == "Title" and "a@b.example" in p.emails


def test_arabic_text_does_not_count_towards_latin_share():
    arabic = "هذا نص عربي بسيط يستخدم لاختبار الكشف عن اللغة في الصفحة"
    assert detect_from_content(arabic + " Some English words") == "unknown"
