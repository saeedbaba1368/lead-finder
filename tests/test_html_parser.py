"""Phase 7.1: HTML parser unit tests. Fixtures are inline HTML strings; nothing touches the network."""

from __future__ import annotations

import logging

import pytest

from app.crawler import FetchOutcome, PageResult
from app.crawler import html_parser
from app.crawler.html_parser import (
    Heading, PageMetadata, ParsedPage, decode_html, is_html_content_type, parse_html, parse_response,
)

FULL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>  Acme Corp |  Home  </title>
  <meta name="description" content="We build anvils.">
  <meta name="keywords" content="anvils, rockets">
  <meta name="robots" content="index, follow">
  <meta property="og:title" content="Acme OG">
  <meta property="og:description" content="OG description">
  <meta property="og:url" content="https://acme.example/">
  <link rel="canonical" href="https://acme.example/home">
  <style>body { color: red; }</style>
  <script>var secret = "not visible";</script>
</head>
<body>
  <h1>Welcome to Acme</h1>
  <p>First   paragraph
     with   odd spacing.</p>
  <h2>Products</h2>
  <ul><li><a href="/products/anvil">Anvil</a></li><li><a href="https://acme.example/rocket">Rocket</a></li></ul>
  <noscript>Enable JavaScript</noscript>
  <template><p>template text</p></template>
</body>
</html>"""


def result(body: bytes, *, content_type: str | None = "text/html", charset: str | None = "utf-8",
           outcome: FetchOutcome = FetchOutcome.OK) -> PageResult:
    return PageResult(
        url="http://example.test/", final_url="http://example.test/", outcome=outcome,
        status_code=200 if outcome is FetchOutcome.OK else 500,
        content_type=content_type, charset=charset, body=body, size=len(body),
    )


# ------------------------------------------------------------------ 1. valid HTML
def test_valid_html_full_page():
    page = parse_html(FULL)
    assert isinstance(page, ParsedPage)
    assert page.title == "Acme Corp | Home"
    assert page.text == "Welcome to Acme\nFirst paragraph with odd spacing.\nProducts\nAnvil\nRocket"
    assert page.headings == (Heading(1, "Welcome to Acme"), Heading(2, "Products"))
    assert page.links == ("/products/anvil", "https://acme.example/rocket")
    assert page.metadata == PageMetadata(
        description="We build anvils.", keywords="anvils, rockets", canonical_url="https://acme.example/home",
        robots="index, follow", og_title="Acme OG", og_description="OG description", og_url="https://acme.example/",
    )


def test_to_dict_is_json_ready():
    import json

    data = parse_html(FULL).to_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data
    assert data["headings"][0] == {"level": 1, "text": "Welcome to Acme"}


# ------------------------------------------------------------------ 2/3. title
@pytest.mark.parametrize("html", ["<html><body>x</body></html>", "<p>no head at all</p>"])
def test_missing_title_is_none(html):
    assert parse_html(html).title is None


@pytest.mark.parametrize("html", ["<title></title>", "<title>   \n\t </title>", "<title/>"])
def test_empty_title_is_none(html):
    assert parse_html(f"<head>{html}</head><body>x</body>").title is None


def test_title_whitespace_is_normalised():
    assert parse_html("<title>\n   Hello \t  World \n</title>").title == "Hello World"


def test_only_first_title_counts_and_title_is_not_body_text():
    page = parse_html("<title>First</title><body>Body<title>Second</title></body>")
    assert page.title == "First"
    assert page.text == "Body"


# ------------------------------------------------------------------ 4. malformed HTML
@pytest.mark.parametrize(
    "html",
    [
        "<html><head><title>Broken<body><h1>Heading<p>Para<div>Unclosed",
        "</div></span><p>stray end tags</b></p>",
        "<a href=\"/x\">link</a><a href=",
        "<<<>>><p<div>>text",
        "<h1><h2>nested</h1></h2><b><i>cross</b></i>",
        "<!-- unterminated comment <p>hidden",
        "\x00\x01 weird \ufffd bytes",
    ],
)
def test_malformed_html_never_crashes(html):
    page = parse_html(html)
    assert isinstance(page, ParsedPage)


def test_malformed_unclosed_title_does_not_swallow_the_document():
    page = parse_html("<html><head><title>Broken<body><h1>Heading</h1><p>Para</p>")
    assert page.title == "Broken"
    assert page.headings == (Heading(1, "Heading"),)
    assert "Para" in page.text


def test_malformed_keeps_what_was_found_before_the_damage():
    page = parse_html('<title>T</title><a href="/ok">x</a><p>fine</p><a href="/cut')
    assert page.title == "T" and "/ok" in page.links and "fine" in page.text


# ------------------------------------------------------------------ 5. visible text
def test_visible_text_inline_elements_do_not_split_words():
    assert parse_html("<p>he<b>ll</b>o <i>wor</i>ld</p>").text == "hello world"


def test_visible_text_blocks_become_lines():
    page = parse_html("<div>one</div><div>two<br>three</div><ul><li>four</li><li>five</li></ul>")
    assert page.text == "one\ntwo\nthree\nfour\nfive"


def test_visible_text_whitespace_is_collapsed_not_rewritten():
    page = parse_html("<p>  a \n\n\t b   </p>\n\n\n<p>\u00a0c\u00a0</p>")
    assert page.text == "a b\nc"


def test_comments_are_not_text():
    assert parse_html("<p>a<!-- hidden -->b</p>").text == "ab"


# ------------------------------------------------------------------ 6. script/style/noscript/template removal
@pytest.mark.parametrize("tag", ["script", "style", "noscript", "template"])
def test_non_visible_elements_are_removed(tag):
    page = parse_html(f"<p>before</p><{tag}>HIDDEN <b>inner</b></{tag}><p>after</p>")
    assert page.text == "before\nafter"
    assert "HIDDEN" not in page.text and "inner" not in page.text


def test_script_content_with_markup_like_text_stays_hidden():
    page = parse_html('<p>v</p><script>document.write("<h1>x</h1><a href=\'/z\'>");</script><p>w</p>')
    assert page.text == "v\nw" and page.headings == () and page.links == ()


def test_nested_hidden_elements_and_elements_inside_them():
    page = parse_html(
        "<p>a</p><noscript><template><script>x</script>t</template><h2>h</h2>"
        '<a href="/in-noscript">l</a></noscript><p>b</p>'
    )
    assert page.text == "a\nb" and page.headings == () and page.links == ()


def test_hidden_content_does_not_leak_into_headings():
    page = parse_html("<h1>Title <script>evil()</script>Rest</h1>")
    assert page.headings == (Heading(1, "Title Rest"),)


# ------------------------------------------------------------------ 7. headings
def test_headings_all_levels_in_document_order():
    html = "".join(f"<h{n}>Level {n}</h{n}>" for n in (3, 1, 6, 2, 5, 4)) + "<h1>again</h1>"
    levels = [(h.level, h.text) for h in parse_html(html).headings]
    assert levels == [(3, "Level 3"), (1, "Level 1"), (6, "Level 6"), (2, "Level 2"), (5, "Level 5"), (4, "Level 4"), (1, "again")]


def test_headings_text_is_normalised_and_includes_inline_children():
    page = parse_html("<h2>  Hello \n <em>big</em>   world  </h2>")
    assert page.headings == (Heading(2, "Hello big world"),)


def test_empty_headings_are_skipped_and_unclosed_heading_still_counts():
    page = parse_html("<h1></h1><h2>  </h2><h3>Kept<p>after")
    assert page.headings == (Heading(3, "Kept"),)


# ------------------------------------------------------------------ 8-10. links
def test_link_extraction_raw_values_document_order_deduplicated():
    page = parse_html(
        '<a href="/b">1</a><a href="/a">2</a><a href="/b">3</a><a>no href</a><a href="">e</a><a href="   ">w</a>'
        '<a href="mailto:x@example.com">m</a><a href="#top">f</a><area href="/area">'
    )
    assert page.links == ("/b", "/a", "mailto:x@example.com", "#top")  # raw: no filtering of schemes/fragments


def test_relative_links_are_not_resolved_or_normalised():
    hrefs = ["/root", "child/page.html", "../up", "./same", "?q=1", "//cdn.example.com/x", "a/../b//c"]
    page = parse_html("".join(f'<a href="{h}">x</a>' for h in hrefs))
    assert page.links == tuple(hrefs)


def test_absolute_links_are_not_normalised():
    hrefs = ["https://Example.COM:443/Path/?b=2&a=1#frag", "HTTP://upper.example/", "http://127.0.0.1:8080/x"]
    page = parse_html("".join(f'<a href="{h}">x</a>' for h in hrefs))
    assert page.links == tuple(hrefs)


def test_link_values_are_stripped():
    assert parse_html('<a href="  /spaced \n">x</a>').links == ("/spaced",)


def test_link_count_is_capped():
    html = "".join(f'<a href="/{i}">x</a>' for i in range(html_parser.MAX_LINKS_PER_PAGE + 50))
    assert len(parse_html(html).links) == html_parser.MAX_LINKS_PER_PAGE


# ------------------------------------------------------------------ 11-14. metadata
def test_missing_metadata_is_none():
    assert parse_html("<html><head></head><body>x</body></html>").metadata == PageMetadata()


def test_metadata_names_are_case_insensitive_and_values_normalised():
    page = parse_html('<meta NAME="Description" CONTENT="  a   b \n c "><meta name="KEYWORDS" content="k1,k2">')
    assert page.metadata.description == "a b c" and page.metadata.keywords == "k1,k2"


def test_blank_metadata_content_is_none_and_first_non_blank_wins():
    page = parse_html(
        '<meta name="description" content="  "><meta name="description" content="first">'
        '<meta name="description" content="second"><meta name="keywords"><meta name="robots" content="">'
    )
    assert page.metadata.description == "first"
    assert page.metadata.keywords is None and page.metadata.robots is None


@pytest.mark.parametrize(
    "link, expected",
    [
        ('<link rel="canonical" href="https://e.example/c">', "https://e.example/c"),
        ('<link rel="CANONICAL" href=" /relative/c ">', "/relative/c"),  # raw, stripped, not resolved
        ('<link rel="alternate canonical" href="/multi">', "/multi"),
        ('<link rel="stylesheet" href="/s.css">', None),
        ('<link rel="canonical">', None),
        ('<link rel="canonical" href="">', None),
    ],
)
def test_canonical_url(link, expected):
    assert parse_html(f"<head>{link}</head>").metadata.canonical_url == expected


def test_canonical_first_wins_and_ignored_inside_noscript():
    page = parse_html('<noscript><link rel="canonical" href="/no"></noscript><link rel="canonical" href="/1"><link rel="canonical" href="/2">')
    assert page.metadata.canonical_url == "/1"


def test_open_graph_metadata():
    page = parse_html(
        '<meta property="og:title" content="T"><meta property="og:description" content="D">'
        '<meta property="og:url" content="https://e.example/u"><meta property="og:image" content="/i.png">'
    )
    assert (page.metadata.og_title, page.metadata.og_description, page.metadata.og_url) == ("T", "D", "https://e.example/u")


def test_open_graph_via_name_attribute_is_accepted():
    assert parse_html('<meta name="og:title" content="via name">').metadata.og_title == "via name"


@pytest.mark.parametrize("content", ["noindex, nofollow", "NOINDEX", "none"])
def test_robots_meta(content):
    assert parse_html(f'<meta name="robots" content="{content}">').metadata.robots == content


def test_other_robots_like_meta_names_are_not_confused():
    page = parse_html('<meta name="googlebot" content="noindex"><meta name="viewport" content="width=device-width">')
    assert page.metadata.robots is None


# ------------------------------------------------------------------ 15/16. Persian and mixed text
def test_persian_text_is_preserved():
    page = parse_html("<title>صفحه اصلی</title><h1>سلام دنیا</h1><p>این یک متن آزمایشی است.</p>")
    assert page.title == "صفحه اصلی"
    assert page.headings == (Heading(1, "سلام دنیا"),)
    assert page.text == "سلام دنیا\nاین یک متن آزمایشی است."


def test_persian_zwnj_and_digits_are_not_treated_as_whitespace():
    text = "می\u200cخواهم ۱۲۳ و ٤٥٦"  # ZWNJ, Persian digits, Arabic-Indic digits
    page = parse_html(f"<p>{text}</p>")
    assert page.text == text and "\u200c" in page.text


def test_mixed_persian_english_content():
    html = (
        '<title>Acme | شرکت آکمه</title><h2>Contact تماس با ما</h2>'
        '<p>Email: info@acme.example — تلفن: ۰۲۱-۱۲۳۴۵۶۷۸</p><a href="/fa/درباره">درباره ما</a>'
        '<meta name="description" content="Anvils | سندان">'
    )
    page = parse_html(html)
    assert page.title == "Acme | شرکت آکمه"
    assert page.headings == (Heading(2, "Contact تماس با ما"),)
    assert page.text == "Contact تماس با ما\nEmail: info@acme.example — تلفن: ۰۲۱-۱۲۳۴۵۶۷۸\nدرباره ما"
    assert page.links == ("/fa/درباره",)
    assert page.metadata.description == "Anvils | سندان"


# ------------------------------------------------------------------ 17. entities
def test_html_entities_are_decoded_everywhere():
    page = parse_html(
        '<title>Tom &amp; Jerry &copy; 2026</title><h1>&lt;b&gt; &#1587;&#x644;&#x627;&#x645;</h1>'
        '<p>a&nbsp;b &quot;q&quot; &euro;5</p><meta name="description" content="x &amp; y">'
        '<a href="/search?a=1&amp;b=2">s</a>'
    )
    assert page.title == "Tom & Jerry © 2026"
    assert page.headings == (Heading(1, "<b> سلام"),)
    assert page.text == '<b> سلام\na b "q" €5\ns'
    assert page.metadata.description == "x & y"
    assert page.links == ("/search?a=1&b=2",)


def test_unknown_or_broken_entities_do_not_crash():
    assert parse_html("<p>&notanentity; &#99999999; &amp</p>").text != ""


# ------------------------------------------------------------------ 18. non-HTML responses
@pytest.mark.parametrize("content_type", ["application/json", "image/png", "application/pdf", "text/plain", "text/css"])
def test_non_html_response_is_not_parsed(content_type):
    assert parse_response(result(b"<html><title>looks like html</title></html>", content_type=content_type)) is None


def test_binary_body_without_content_type_is_not_parsed():
    assert parse_response(result(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", content_type=None, charset=None)) is None


def test_missing_content_type_with_text_body_is_parsed():
    page = parse_response(result(b"<title>No header</title>", content_type=None, charset=None))
    assert page is not None and page.title == "No header"


def test_failed_fetch_is_not_parsed():
    assert parse_response(result(b"<html></html>", outcome=FetchOutcome.HTTP_ERROR)) is None


@pytest.mark.parametrize("content_type", ["text/html", "TEXT/HTML", "text/html; charset=utf-8", "application/xhtml+xml", " text/html ;x=y"])
def test_is_html_content_type_accepts(content_type):
    assert is_html_content_type(content_type)


@pytest.mark.parametrize("content_type", [None, "", "text/plain", "application/json", "text/htmlx", "image/svg+xml"])
def test_is_html_content_type_rejects(content_type):
    assert not is_html_content_type(content_type)


def test_xhtml_response_is_parsed():
    page = parse_response(result(b"<html><head><title>X</title></head><body><p>y</p></body></html>", content_type="application/xhtml+xml"))
    assert page is not None and (page.title, page.text) == ("X", "y")


# ------------------------------------------------------------------ 19. empty response
def test_empty_response_is_not_parsed():
    assert parse_response(result(b"")) is None


@pytest.mark.parametrize("html", ["", "   \n\t ", "<html></html>", "<!DOCTYPE html>"])
def test_empty_document_gives_empty_page(html):
    assert parse_html(html) == ParsedPage()


# ------------------------------------------------------------------ 20. parser failure handling
def test_parser_failure_returns_partial_result_and_logs(monkeypatch, caplog):
    original_feed = html_parser._PageParser.feed

    def exploding_feed(self, data):
        original_feed(self, "<title>Partial</title><p>seen</p>")
        raise RuntimeError("boom")

    monkeypatch.setattr(html_parser._PageParser, "feed", exploding_feed)
    with caplog.at_level(logging.WARNING):
        page = parse_html("<html>ignored</html>")
    assert page.title == "Partial" and page.text == "seen"
    assert any(r.getMessage() == "html_parse_partial" for r in caplog.records)


def test_parser_failure_on_total_breakdown_gives_empty_page(monkeypatch):
    def exploding_feed(self, data):
        raise ValueError("boom")

    monkeypatch.setattr(html_parser._PageParser, "feed", exploding_feed)
    assert parse_html("<p>x</p>") == ParsedPage()


def test_parse_html_rejects_bytes():
    with pytest.raises(TypeError):
        parse_html(b"<p>bytes</p>")  # type: ignore[arg-type]


# ------------------------------------------------------------------ encoding
def test_decode_utf8_persian():
    assert decode_html("سلام دنیا Hello".encode("utf-8"), "utf-8") == "سلام دنیا Hello"


def test_decode_uses_http_charset():
    body = "<p>سلام</p>".encode("cp1256")
    assert decode_html(body, "windows-1256") == "<p>سلام</p>"
    assert parse_response(result(body, charset="windows-1256")).text == "سلام"


def test_decode_uses_meta_charset_when_header_has_none():
    body = '<meta charset="windows-1256"><p>سلام</p>'.encode("cp1256")
    assert parse_response(result(body, charset=None)).text == "سلام"


def test_decode_uses_http_equiv_meta_charset():
    body = '<meta http-equiv="Content-Type" content="text/html; charset=windows-1256"><p>سلام</p>'.encode("cp1256")
    assert parse_html(decode_html(body, None)).text == "سلام"


def test_decode_utf8_bom_and_utf16_bom():
    assert decode_html(b"\xef\xbb\xbf" + "سلام".encode("utf-8"), "iso-8859-1") == "سلام"
    assert decode_html("<p>سلام</p>".encode("utf-16"), None) == "<p>سلام</p>"


def test_decode_unknown_charset_falls_back_to_utf8():
    assert decode_html("سلام".encode("utf-8"), "no-such-charset") == "سلام"


def test_decode_wrong_declared_charset_falls_through_to_next_candidate():
    body = '<meta charset="utf-8"><p>سلام</p>'.encode("utf-8")
    assert decode_html(body, "ascii") == '<meta charset="utf-8"><p>سلام</p>'  # ascii cannot decode it; the meta label can


def test_decode_invalid_bytes_are_replaced_not_raised():
    assert "\ufffd" in decode_html(b"<p>ok \xff\xfe bad</p>", None)
    assert parse_response(result(b"<p>ok \xff\xfe bad</p>", charset=None)).text.startswith("ok")
