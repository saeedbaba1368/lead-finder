"""Phase 7.2.1: email extraction from visible text and mailto: links (pure parsing, no network)."""

from __future__ import annotations

import json

from app.crawler import ParsedPage, parse_html


def emails(html: str) -> tuple[str, ...]:
    return parse_html(html).emails


# 1. email in visible text
def test_email_in_visible_text():
    assert emails("<p>Contact us: hello@example.com today</p>") == ("hello@example.com",)


def test_email_split_across_whitespace_and_markup_is_normalised():
    assert emails("<div>\n  Write to\n <b>info@example.com</b>.\n</div>") == ("info@example.com",)


# 2. mailto link
def test_mailto_link():
    assert emails('<a href="mailto:hello@example.com">Email us</a>') == ("hello@example.com",)


def test_mailto_with_query_case_and_multiple_recipients():
    html = '<a href="MAILTO:Sales@Example.com?subject=Hi%20there&body=x">go</a><a href="mailto:a@ex.org,b@ex.org">more</a>'
    assert emails(html) == ("a@ex.org", "b@ex.org", "sales@example.com")


def test_mailto_without_valid_address_is_ignored():
    assert emails('<a href="mailto:">x</a><a href="mailto:not-an-email">y</a>') == ()


# 3. multiple emails: deterministic order (sorted, lower-case)
def test_multiple_emails_are_sorted_deterministically():
    html = "<p>z@example.com</p><p>info@example.com</p><p>Hello@Example.com</p>"
    assert emails(html) == ("hello@example.com", "info@example.com", "z@example.com")


def test_same_result_for_same_input():
    html = '<p>b@x.com a@x.com</p><a href="mailto:c@x.com">c</a>'
    assert emails(html) == emails(html) == ("a@x.com", "b@x.com", "c@x.com")


# 4. duplicates
def test_duplicates_across_text_and_mailto_and_case():
    html = (
        '<p>hello@example.com</p><p>HELLO@example.com</p><a href="mailto:hello@example.com">hello@example.com</a>'
    )
    assert emails(html) == ("hello@example.com",)


# 5. invalid email-like strings
def test_invalid_email_like_strings_are_rejected():
    html = (
        "<p>user@localhost user@ @example.com a@b a@@example.com a..b@example.com "
        "user@example..com user@-example.com user@example.c logo@2x.png sprite@icons.svg "
        "name@example.com-x @handle just@ text</p>"
    )
    assert emails(html) == ()


def test_valid_address_next_to_junk_is_still_found():
    assert emails("<p>see (info@example.com), or @twitter_handle; logo@2x.png</p>") == ("info@example.com",)


def test_trailing_punctuation_is_not_part_of_address():
    assert emails("<p>Mail info@example.com. Or sales@example.co.uk!</p>") == ("info@example.com", "sales@example.co.uk")


# 6. script / style / comments
def test_email_inside_script_and_style_is_ignored():
    html = (
        "<style>.x::after{content:'css@example.com'}</style>"
        "<script>var m = 'js@example.com'; document.write('<a href=\"mailto:js2@example.com\">');</script>"
        "<noscript>ns@example.com</noscript><template>tpl@example.com</template>"
        "<p>visible@example.com</p>"
    )
    assert emails(html) == ("visible@example.com",)


def test_email_inside_html_comment_is_ignored():
    html = '<p>a</p><!-- hidden@example.com --><!-- <a href="mailto:h2@example.com">x</a> --><p>shown@example.com</p>'
    assert emails(html) == ("shown@example.com",)


# 7. Persian / mixed-language page
def test_persian_and_mixed_language_page():
    html = (
        '<html lang="fa"><head><meta charset="utf-8"><title>تماس با ما</title></head><body>'
        "<h1>تماس با ما</h1><p>ایمیل: info@example.ir لطفاً پیام بگذارید</p>"
        "<p>ایمیل‌ما:support@example.com،</p>"
        '<p><a href="mailto:sales@example.ir">ارسال ایمیل</a> Contact: Hello@Example.com</p>'
        "<p>ایمیل@فارسی.com</p></body></html>"
    )
    page = parse_html(html)
    assert page.emails == ("hello@example.com", "info@example.ir", "sales@example.ir", "support@example.com")
    assert "تماس با ما" in page.text  # existing Persian text handling is unchanged


# 8. page without email
def test_page_without_email():
    page = parse_html("<html><body><h1>Hi</h1><p>No contact here</p><a href='/about'>About</a></body></html>")
    assert page.emails == ()


def test_empty_document_has_no_emails():
    assert parse_html("").emails == ()
    assert ParsedPage().emails == ()


# integration with the existing model
def test_to_dict_includes_emails_and_is_json_ready():
    data = parse_html("<p>a@example.com</p>").to_dict()
    assert data["emails"] == ["a@example.com"]
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data


def test_mailto_link_remains_in_links_and_existing_fields_untouched():
    page = parse_html('<title>T</title><p>x</p><a href="mailto:a@example.com">m</a><a href="/p">p</a>')
    assert page.links == ("mailto:a@example.com", "/p")
    assert page.title == "T"
    assert page.text == "x\nmp"
    assert page.emails == ("a@example.com",)
