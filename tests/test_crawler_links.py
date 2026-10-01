"""Pure link-extraction tests (no network)."""

from app.crawler.links import MAX_LINKS_PER_PAGE, extract_links

PAGE = "http://site.example/dir/page"


def test_collects_a_and_area_in_document_order_and_dedupes():
    html = '<a href="/x">1</a><area href="/map"><a href="/x">dup</a><a href="rel.html">2</a>'
    r = extract_links(html, PAGE)
    assert r.hrefs == ("/x", "/map", "rel.html")
    assert r.base_url == PAGE


def test_skips_non_navigational_links_and_counts_them():
    html = (
        '<a href="mailto:a@b.c">m</a><a href="tel:123">t</a><a href="javascript:void(0)">j</a>'
        '<a href="data:text/html,x">d</a><a href="ftp://h/f">f</a><a href="#top">frag</a>'
        '<a href="">e</a><a href="   ">e2</a><a>none</a><a href="HTTP://Ok.example/">ok</a>'
    )
    r = extract_links(html, PAGE)
    assert r.hrefs == ("HTTP://Ok.example/",)
    assert r.skipped_non_http == 5 and r.skipped_fragment_only == 1 and r.skipped_empty == 2


def test_href_whitespace_is_stripped_and_charrefs_decoded():
    r = extract_links('<a href="  /a?x=1&amp;y=2 ">l</a>', PAGE)
    assert r.hrefs == ("/a?x=1&y=2",)


def test_base_href_is_resolved_and_first_one_wins():
    r = extract_links('<base href="/sub/"><base href="/other/"><a href="a">x</a>', PAGE)
    assert r.base_url == "http://site.example/sub/"


def test_invalid_base_href_is_ignored():
    assert extract_links('<base href="mailto:x@y.z"><a href="a">x</a>', PAGE).base_url == PAGE
    assert extract_links('<base href="http://[bad"><a href="a">x</a>', PAGE).base_url == PAGE


def test_malformed_html_does_not_raise():
    for html in ("", "<<<a href='x' <div", "<a href=/unquoted>u</a", "\x00<a href=/z>", "<a href='/q"):
        extract_links(html, PAGE)
    assert "/unquoted" in extract_links("<a href=/unquoted>u</a>", PAGE).hrefs


def test_malformed_urls_are_passed_through_for_the_url_engine_to_reject():
    r = extract_links('<a href="http://[bad">x</a><a href="http://">y</a>', PAGE)
    assert r.hrefs == ("http://[bad", "http://")


def test_link_cap():
    html = "".join(f'<a href="/p{i}">x</a>' for i in range(MAX_LINKS_PER_PAGE + 5))
    r = extract_links(html, PAGE)
    assert len(r.hrefs) == MAX_LINKS_PER_PAGE and r.truncated
