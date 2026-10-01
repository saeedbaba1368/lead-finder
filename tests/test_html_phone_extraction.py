"""Phase 7.2.2: phone extraction from visible text and tel: links (pure parsing, no network)."""

from __future__ import annotations

import json

from app.crawler import ParsedPage, PhoneNumber, parse_html


def numbers(html: str) -> tuple[str, ...]:
    return tuple(p.number for p in parse_html(html).phones)


# 1. phone in visible text
def test_phone_in_visible_text():
    page = parse_html("<p>Call us on 555-123-4567 today</p>")
    assert page.phones == (PhoneNumber("5551234567", "555-123-4567"),)


def test_label_allows_plain_digit_run_and_parentheses_format():
    assert numbers("<p>Tel: 12345678</p>") == ("12345678",)
    assert numbers("<p>(555) 123-4567</p>") == ("5551234567",)


# 2. tel link
def test_tel_link():
    page = parse_html('<a href="tel:+1-555-123-4567">Call</a>')
    assert page.phones == (PhoneNumber("+15551234567", "+1-555-123-4567"),)


def test_tel_link_params_encoding_and_invalid_values():
    assert numbers('<a href="TEL:%2B98%2021%201234%205678;ext=12">x</a>') == ("+982112345678",)
    assert numbers('<a href="tel:+1-800-FLOWERS">x</a><a href="tel:">y</a><a href="tel:12">z</a>') == ()


# 3. multiple phones: deterministic order (sorted by normalised number)
def test_multiple_phones_sorted_deterministically():
    html = "<p>Sales +44 20 7946 0958</p><p>Office 021-12345678</p><p>Mobile +1 (415) 555-2671</p>"
    assert numbers(html) == ("+14155552671", "+442079460958", "02112345678")
    assert numbers(html) == numbers(html)


def test_two_numbers_in_one_line_are_not_merged():
    assert numbers("<p>555-123-4567, 555-765-4321 or 555-111-2222</p>") == ("5551112222", "5551234567", "5557654321")


# 4. duplicates
def test_duplicates_across_formats_text_and_tel():
    html = (
        '<p>+1 555 123 4567</p><p>+1-555-123-4567</p><a href="tel:+15551234567">+1 (555) 123-4567</a>'
    )
    page = parse_html(html)
    assert [p.number for p in page.phones] == ["+15551234567"]
    assert page.phones[0].raw == "+15551234567"  # first seen wins: tel links come before text


def test_same_number_in_persian_and_ascii_digits_is_one_phone():
    assert numbers("<p>۰۲۱-۱۲۳۴۵۶۷۸</p><p>021-12345678</p>") == ("02112345678",)


# 5. international format
def test_international_formats():
    html = (
        "<p>+98 21 1234 5678</p><p>0044 20 7946 0958</p><p>+49 (0) 30 1234567</p>"
        "<p>+1.555.123.4567</p><p>+12025550143</p>"
    )
    assert numbers(html) == tuple(
        sorted(["+982112345678", "+442079460958", "+49301234567", "+15551234567", "+12025550143"])
    )


# 6. local format
def test_local_formats():
    html = "<p>0912 345 6789</p><p>(021) 1234 5678</p><p>555-1234</p><p>09123456789</p><p>020 7946 0958</p>"
    assert numbers(html) == tuple(sorted(["09123456789", "02112345678", "5551234", "02079460958"]))


def test_raw_value_is_preserved():
    page = parse_html("<p>Phone:   (021)   1234-5678</p>")
    assert page.phones == (PhoneNumber("02112345678", "(021) 1234-5678"),)


# 7. obvious non-phone numbers
def test_obvious_non_phone_numbers_are_rejected():
    html = (
        "<p>Price $1,234,567 and 1 234 567 USD, 1,234.56 total, 12.50, 49.99 EUR.</p>"
        "<p>Date 2024-05-12, 12/05/2024, 12.05.2024, 1403/05/12, 2024-05-12 10:30:45.</p>"
        "<p>Order #12345678 ID: 123456789 SKU 4006381333931 invoice 20240512001.</p>"
        "<p>ZIP 12345 and 12345-6789, postal code 1234567890, کد پستی 12345-67890.</p>"
        "<p>IP 192.168.100.200 version 1.2.3.4567, card 4111 1111 1111 1111, ssn 123-45-6789.</p>"
        "<p>ISBN 978-3-16-148410-0, 1 2 3 4 5 6 7, 00000000, 1234567890, 2019-2020.</p>"
        "<p>Coordinates 35.6892, 51.3890. Year 1403. Size 1200 x 800.</p>"
        '<p><a href="mailto:5551234567@example.com">x</a> 5551234567@example.com</p>'
    )
    assert numbers(html) == ()


def test_phone_next_to_non_phone_numbers_is_still_found():
    html = "<p>Order #12345678 placed 2024-05-12 for $99.99. Questions? Call +1 555 123 4567.</p>"
    assert numbers(html) == ("+15551234567",)


def test_trailing_punctuation_is_not_part_of_number():
    assert numbers("<p>Call 555-123-4567. Or (021) 1234 5678!</p>") == ("02112345678", "5551234567")


# script / style / comments are not visible text
def test_phone_inside_script_style_and_comment_is_ignored():
    html = (
        "<style>.a{content:'555-123-4567'}</style><script>var p='+1 555 000 1111';</script>"
        "<!-- 555-222-3333 --><noscript>555-444-5555</noscript><p>visible 555-666-7777</p>"
    )
    assert numbers(html) == ("5556667777",)


# 8. Persian / mixed-language page
def test_persian_and_mixed_language_page():
    html = (
        '<html lang="fa"><head><meta charset="utf-8"><title>تماس با ما</title></head><body>'
        "<h1>تماس با ما</h1>"
        "<p>تلفن: ۰۲۱-۱۲۳۴۵۶۷۸</p>"
        "<p>موبایل ۰۹۱۲۳۴۵۶۷۸۹ و فکس: +98 21 8765 4321</p>"
        '<p><a href="tel:+989121112233">تماس بگیرید</a> Support: +44 20 7946 0958</p>'
        "<p>قیمت: ۱۲,۰۰۰,۰۰۰ تومان — کد پستی ۱۲۳۴۵-۶۷۸۹۰ — تاریخ ۱۴۰۳/۰۵/۱۲ — سفارش ۱۲۳۴۵۶۷۸</p>"
        "</body></html>"
    )
    page = parse_html(html)
    assert [p.number for p in page.phones] == sorted(
        ["+989121112233", "+982187654321", "+442079460958", "02112345678", "09123456789"]
    )
    by_number = {p.number: p.raw for p in page.phones}
    assert by_number["02112345678"] == "۰۲۱-۱۲۳۴۵۶۷۸"  # original Persian digits preserved in `raw`
    assert "تماس با ما" in page.text  # existing text handling unchanged


# 9. page without phone
def test_page_without_phone():
    page = parse_html("<html><body><h1>Hi</h1><p>No contact here, 3 items, 42%</p><a href='/about'>About</a></body></html>")
    assert page.phones == ()


def test_empty_document_has_no_phones():
    assert parse_html("").phones == ()
    assert ParsedPage().phones == ()


# integration with the existing model
def test_to_dict_includes_phones_and_is_json_ready():
    data = parse_html("<p>+1 555 123 4567</p>").to_dict()
    assert data["phones"] == [{"number": "+15551234567", "raw": "+1 555 123 4567"}]
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data


def test_email_extraction_unchanged_and_tel_stays_in_links():
    page = parse_html('<p>mail info@example.com</p><a href="tel:+15551234567">t</a><a href="mailto:a@example.com">m</a>')
    assert page.emails == ("a@example.com", "info@example.com")
    assert page.links == ("tel:+15551234567", "mailto:a@example.com")
    assert [p.number for p in page.phones] == ["+15551234567"]
