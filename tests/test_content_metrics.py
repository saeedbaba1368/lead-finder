"""Phase 7.3.3: deterministic content-quality metrics (pure counting, no network)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.crawler import ContentMetrics, FetchOutcome, PageResult, ParsedPage, compute_metrics, parse_html, parse_response
from app.crawler.metrics import count_words

FA = "این یک متن فارسی برای آزمایش است و باید درست شمرده شود."
EN = "This is a plain English sentence for testing."


def m(html: str) -> ContentMetrics:
    return parse_html(html).metrics


# 1. empty page
@pytest.mark.parametrize("html", ["", "   \n\t ", "<html></html>", "<html><head></head><body></body></html>"])
def test_empty_page(html):
    got = m(html)
    assert (got.character_count, got.word_count, got.heading_count, got.link_count, got.image_count,
            got.email_count, got.phone_count) == (0, 0, 0, 0, 0, 0, 0)
    assert got.html_length == len(html)
    assert got.text_html_ratio == 0.0


def test_default_metrics_are_zero():
    assert ParsedPage().metrics == ContentMetrics() == compute_metrics(ParsedPage())
    assert ContentMetrics().text_html_ratio == 0.0


# 2. short page
def test_short_page_exact_values():
    html = "<html><body><h1>Hello world</h1><p>Short page.</p></body></html>"
    got = m(html)
    assert got.character_count == len("Hello world\nShort page.")
    assert got.word_count == 4 and got.heading_count == 1
    assert got.html_length == len(html)
    assert got.text_html_ratio == round(got.character_count / len(html), 4)
    assert 0 < got.text_html_ratio < 1


def test_counts_of_emails_and_phones_match_page_fields():
    page = parse_html("<p>Mail a@b.example or c@d.example, call +44 20 7946 0958 or tel <a href='tel:021 1234 5678'>x</a></p>")
    assert page.metrics.email_count == len(page.emails) == 2
    assert page.metrics.phone_count == len(page.phones) >= 1


def test_headings_counted_all_levels():
    assert m("<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5><h6>f</h6><h2></h2>").heading_count == 6


# 3. large page
def test_large_page():
    html = "<html><body>" + "".join(f"<p>Paragraph number {i} with some words.</p>" for i in range(5000)) + "</body></html>"
    got = m(html)
    assert got.word_count == 5000 * 6 and got.html_length == len(html)
    assert 0.3 < got.text_html_ratio < 1


def test_large_page_with_huge_markup_has_low_ratio():
    html = "<p>hi</p>" + "<div class='a b c'></div>" * 20000
    got = m(html)
    assert got.word_count == 1 and got.text_html_ratio < 0.001 and got.html_length == len(html)


# 4. Persian content
def test_persian_content():
    got = m(f"<html><body><h1>عنوان صفحه</h1><p>{FA}</p></body></html>")
    assert got.word_count == 2 + len(FA.split())
    assert got.character_count == len("عنوان صفحه\n" + FA)
    assert got.heading_count == 1


def test_zwnj_joined_word_counts_once_and_digits_count():
    assert m("<p>می\u200cخواهم کتاب ۱۲۳</p>").word_count == 3
    assert count_words("۱۲۳ 456") == 2


def test_non_word_tokens_are_ignored():
    assert count_words("- — ... 😀 ! ، ؛") == 0
    assert count_words("Hello - world, 😀 ok") == 3


# 5. mixed-language
def test_mixed_language_content():
    got = m(f"<p>{FA}</p><p>{EN}</p>")
    assert got.word_count == len(FA.split()) + len(EN.split())
    assert got.character_count == len(FA) + 1 + len(EN)


def test_unicode_characters_counted_as_characters_not_bytes():
    got = m("<p>سلام 🌍</p>")
    assert got.character_count == len("سلام 🌍") and got.html_length == len("<p>سلام 🌍</p>")


def test_parse_response_html_length_is_characters_of_decoded_text():
    html = f"<p>{FA}</p>"
    body = html.encode("utf-8")
    res = PageResult(url="https://a.example/", final_url="https://a.example/", outcome=FetchOutcome.OK, status_code=200,
                     content_type="text/html", charset="utf-8", body=body, size=len(body))
    got = parse_response(res).metrics
    assert got.html_length == len(html) < len(body) and got.character_count == len(FA)


# 6. many links
def test_many_links():
    html = "".join(f"<a href='/p{i}'>l{i}</a>" for i in range(2500))
    got = m(html)
    assert got.link_count == 2500
    assert got.unique_link_count <= 2500  # unique count follows the existing per-page link cap


def test_duplicate_links_counted_in_link_count_but_once_in_unique():
    got = m("<a href='/a'>1</a><a href='/a'>2</a><a href='/b'>3</a><a href='mailto:x@y.example'>m</a>")
    assert (got.link_count, got.unique_link_count) == (4, 3)


def test_anchors_without_href_or_blank_href_are_not_links():
    assert m("<a name='x'>1</a><a href=''>2</a><a href='   '>3</a><a>4</a>").link_count == 0


def test_link_count_uncapped_while_links_capped():
    from app.crawler.links import MAX_LINKS_PER_PAGE
    n = MAX_LINKS_PER_PAGE + 25
    page = parse_html("".join(f"<a href='/p{i}'>x</a>" for i in range(n)))
    assert page.metrics.link_count == n and page.metrics.unique_link_count == len(page.links) == MAX_LINKS_PER_PAGE


# 7. images
def test_images_counted():
    got = m("<img src='a.png'><img src='b.png' alt='b'/><p>x</p><a href='/x'><img src='c.png'></a><img>")
    assert got.image_count == 4


def test_images_in_script_style_noscript_template_not_counted():
    html = "<script>var s = '<img src=x>';</script><noscript><img src='n.gif'></noscript><template><img></template><img src='real.png'>"
    assert m(html).image_count == 1


def test_image_alt_text_is_not_counted_as_words():
    got = m("<img src='a.png' alt='a nice picture'><p>text</p>")
    assert got.image_count == 1 and got.word_count == 1


def test_page_with_no_images_is_zero():
    assert m(f"<p>{EN}</p>").image_count == 0


# 8. malformed HTML
@pytest.mark.parametrize("html", [
    "<html><body><div><p>unclosed <b>tags <i>here",
    "<p>text</p></div></span></b>",
    "<<<>>><a href=><img <p>x",
    "<img src='unterminated",
    "<a href='/x'>no end <h1>heading never closed",
    "<script>unterminated script <p>hidden",
    "<title>never closed <p>body",
    "\x00\x01<p>control chars</p>",
    "<p>" + "<b>" * 3000 + "deep",
])
def test_malformed_html_never_raises_and_is_consistent(html):
    got = m(html)
    assert got.html_length == len(html)
    assert got.character_count >= 0 and got.word_count >= 0 and 0.0 <= got.text_html_ratio <= 1.0
    assert got.word_count <= got.character_count


def test_malformed_values():
    got = m("<html><body><div><p>unclosed <b>tags <i>here<img src=a><a href='/x'>l")
    assert got.word_count == 3 and got.image_count == 1 and got.link_count == 1  # inline <a> text joins "here" + "l"


def test_unclosed_heading_still_counts():
    got = m("<a href='/x'>no end <h1>heading never closed")
    assert got.heading_count == 1 and got.link_count == 1


# model / integration
def test_to_dict_contains_metrics_with_expected_keys():
    d = parse_html("<h1>Hi</h1><img src=a><a href='/x'>x</a>").to_dict()["metrics"]
    assert set(d) == {"character_count", "word_count", "heading_count", "link_count", "unique_link_count",
                      "image_count", "email_count", "phone_count", "html_length", "text_html_ratio"}
    assert d["image_count"] == 1 and d["link_count"] == 1 and d["heading_count"] == 1


def test_metrics_do_not_change_other_fields():
    page = parse_html("<html lang='fa'><title>T</title><h1>عنوان</h1><a href='/x'>l</a><p>a@b.example</p></html>")
    assert page.title == "T" and page.links == ("/x",) and page.emails == ("a@b.example",)
    assert page.language.language == "fa" and page.classification is not None


def test_metrics_are_deterministic():
    html = f"<h1>x</h1><p>{FA} {EN}</p><img src=a><a href='/q'>q</a>"
    assert {repr(parse_html(html).metrics) for _ in range(10)} == {repr(parse_html(html).metrics)}


def test_compute_metrics_directly_and_replace_keeps_it():
    page = parse_html("<p>one two three</p>")
    assert compute_metrics(page, html_length=100).text_html_ratio == round(len(page.text) / 100, 4)
    assert replace(page, encoding="utf-8").metrics == page.metrics


def test_ratio_never_exceeds_one():
    assert compute_metrics(parse_html("<p>abc</p>"), html_length=1).text_html_ratio == 1.0


def test_html_length_and_ratio_are_ignored_by_equality_but_kept():
    a, b = parse_html("   \n\t "), ParsedPage()
    assert a == b and a.metrics.html_length == 6 and b.metrics.html_length == 0
    assert parse_html("<p>x</p>").metrics != parse_html("<p>y z</p>").metrics
