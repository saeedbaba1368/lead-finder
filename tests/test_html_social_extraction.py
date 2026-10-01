"""Phase 7.2.3: social profile link extraction from <a href> (pure parsing, no network, nothing is crawled)."""

from __future__ import annotations

import json

import pytest

from app.crawler import ParsedPage, SocialLink, parse_html
from app.crawler.links import MAX_LINKS_PER_PAGE
from app.crawler.social import SocialPlatform, classify_social_url, extract_social_links


def page_of(*hrefs: str) -> ParsedPage:
    return parse_html("<body>" + "".join(f'<a href="{h}">x</a>' for h in hrefs) + "</body>")


def socials(*hrefs: str) -> tuple[tuple[str, str], ...]:
    return tuple((s.platform, s.url) for s in page_of(*hrefs).social_links)


# 1. each supported platform (and its common URL variations)
@pytest.mark.parametrize(
    "href, expected",
    [
        ("https://www.facebook.com/acme", ("facebook", "https://www.facebook.com/acme")),
        ("http://facebook.com/acme.corp/", ("facebook", "https://www.facebook.com/acme.corp")),
        ("https://m.facebook.com/acme", ("facebook", "https://www.facebook.com/acme")),
        ("https://fb.me/acme", ("facebook", "https://www.facebook.com/acme")),
        ("https://www.facebook.com/profile.php?id=100012345", ("facebook", "https://www.facebook.com/profile.php?id=100012345")),
        ("https://www.facebook.com/pages/Acme-Corp/123456789", ("facebook", "https://www.facebook.com/pages/Acme-Corp/123456789")),
        ("https://www.facebook.com/groups/acmefans", ("facebook", "https://www.facebook.com/groups/acmefans")),
        ("https://instagram.com/acme_official", ("instagram", "https://www.instagram.com/acme_official")),
        ("https://www.instagram.com/acme.official/", ("instagram", "https://www.instagram.com/acme.official")),
        ("https://www.linkedin.com/company/acme-corp", ("linkedin", "https://www.linkedin.com/company/acme-corp")),
        ("https://www.linkedin.com/in/jane-doe/", ("linkedin", "https://www.linkedin.com/in/jane-doe")),
        ("https://uk.linkedin.com/in/jane-doe", ("linkedin", "https://www.linkedin.com/in/jane-doe")),
        ("https://www.linkedin.com/school/acme-university", ("linkedin", "https://www.linkedin.com/school/acme-university")),
        ("https://twitter.com/acme", ("x", "https://x.com/acme")),
        ("https://x.com/acme_corp", ("x", "https://x.com/acme_corp")),
        ("https://mobile.twitter.com/acme", ("x", "https://x.com/acme")),
        ("https://www.youtube.com/@acmetv", ("youtube", "https://www.youtube.com/@acmetv")),
        ("https://youtube.com/channel/UCabcdefghijklmnopqrstuv", ("youtube", "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv")),
        ("https://www.youtube.com/c/AcmeTV", ("youtube", "https://www.youtube.com/c/AcmeTV")),
        ("https://m.youtube.com/user/acmetv", ("youtube", "https://www.youtube.com/user/acmetv")),
        ("https://www.tiktok.com/@acme", ("tiktok", "https://www.tiktok.com/@acme")),
        ("https://tiktok.com/@acme.official/", ("tiktok", "https://www.tiktok.com/@acme.official")),
        ("//www.instagram.com/acme", ("instagram", "https://www.instagram.com/acme")),  # protocol-relative
        ("HTTPS://WWW.FACEBOOK.COM/Acme", ("facebook", "https://www.facebook.com/Acme")),  # host/scheme case
    ],
)
def test_each_supported_platform(href, expected):
    assert socials(href) == (expected,)


# 2. multiple platforms: deterministic order (platform, then url)
def test_multiple_platforms_sorted_deterministically():
    hrefs = [
        "https://www.tiktok.com/@acme", "https://twitter.com/acme", "https://www.youtube.com/@acme",
        "https://www.linkedin.com/company/acme", "https://instagram.com/acme", "https://facebook.com/acme",
    ]
    page = page_of(*hrefs)
    assert [s.platform for s in page.social_links] == ["facebook", "instagram", "linkedin", "tiktok", "x", "youtube"]
    assert page_of(*reversed(hrefs)).social_links == page.social_links  # input order does not matter


def test_same_platform_links_are_sorted():
    assert socials("https://x.com/zed", "https://x.com/alpha") == (("x", "https://x.com/alpha"), ("x", "https://x.com/zed"))


# 3. duplicate social links
def test_duplicates_across_variations_collapse_to_one():
    hrefs = (
        "https://www.facebook.com/acme", "http://facebook.com/acme/", "https://m.facebook.com/acme?ref=x",
        "https://fb.me/ACME", "//www.facebook.com/acme#top", "https://www.facebook.com/acme",
    )
    assert socials(*hrefs) == (("facebook", "https://www.facebook.com/acme"),)


def test_twitter_and_x_are_the_same_profile():
    assert socials("https://twitter.com/acme", "https://x.com/Acme") == (("x", "https://x.com/acme"),)  # first seen kept


# 4. query strings and fragments
def test_query_strings_and_fragments_are_stripped():
    hrefs = (
        "https://www.instagram.com/acme/?hl=en&utm_source=ig_web_button_share_sheet#bio",
        "https://twitter.com/acme?lang=fa&ref_src=twsrc%5Egoogle",
        "https://www.youtube.com/@acme?sub_confirmation=1",
        "https://www.linkedin.com/company/acme/?originalSubdomain=de",
        "https://www.tiktok.com/@acme?lang=en#x",
    )
    assert socials(*hrefs) == (
        ("instagram", "https://www.instagram.com/acme"), ("linkedin", "https://www.linkedin.com/company/acme"),
        ("tiktok", "https://www.tiktok.com/@acme"), ("x", "https://x.com/acme"), ("youtube", "https://www.youtube.com/@acme"),
    )


def test_facebook_profile_php_keeps_only_the_id_and_legacy_hashbang_twitter_works():
    assert socials("https://www.facebook.com/profile.php?id=1000&sk=info&ref=x") == (
        ("facebook", "https://www.facebook.com/profile.php?id=1000"),
    )
    assert socials("https://www.facebook.com/profile.php?id=abc", "https://www.facebook.com/profile.php") == ()
    assert socials("https://twitter.com/#!/acme") == (("x", "https://x.com/acme"),)


# 5. non-social URLs and non-profile social URLs
@pytest.mark.parametrize(
    "href",
    [
        "https://example.com/", "https://example.com/facebook.com/acme", "/about", "contact.html",
        "https://notfacebook.com/acme", "https://facebook.com.evil.example/acme", "https://evil.example/?u=https://x.com/acme",
        "https://developers.facebook.com/acme", "https://help.instagram.com/acme", "https://api.twitter.com/acme",
        "https://music.youtube.com/@acme", "https://vm.tiktok.com/ZMabc123/", "https://youtu.be/dQw4w9WgXcQ",
        # share buttons, content and utility pages are not profiles
        "https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fexample.com", "https://www.facebook.com/sharer.php?u=x",
        "https://www.facebook.com/share.php", "https://www.facebook.com/login", "https://www.facebook.com/acme/posts/123",
        "https://www.facebook.com/", "https://www.facebook.com/plugins/like.php?href=x", "https://www.facebook.com/dialog/share",
        "https://www.instagram.com/p/Cabc123/", "https://www.instagram.com/explore/tags/acme/", "https://www.instagram.com/",
        "https://www.instagram.com/accounts/login/", "https://www.instagram.com/reel/Cabc123/",
        "https://twitter.com/intent/tweet?text=hi", "https://twitter.com/share?url=x", "https://x.com/acme/status/123456",
        "https://twitter.com/home", "https://twitter.com/i/web/status/1", "https://x.com/",
        "https://www.linkedin.com/shareArticle?mini=true&url=x", "https://www.linkedin.com/sharing/share-offsite/?url=x",
        "https://www.linkedin.com/feed/", "https://www.linkedin.com/company/acme/jobs", "https://www.linkedin.com/in/",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/",
        "https://www.youtube.com/channel/short", "https://www.youtube.com/shorts/abc", "https://www.youtube.com/@acme/videos",
        "https://www.tiktok.com/@acme/video/123", "https://www.tiktok.com/", "https://www.tiktok.com/discover",
        "https://facebook.com:8443/acme",
    ],
)
def test_non_social_and_non_profile_urls_are_rejected(href):
    assert classify_social_url(href) is None
    assert page_of(href).social_links == ()


# 6. malformed links never raise and never match
@pytest.mark.parametrize(
    "href",
    [
        "", "   ", "http://", "https:///facebook.com/acme", "//", "http://[bad-host/acme", "http://[::1", "https://",
        "javascript:alert('https://facebook.com/acme')", "mailto:acme@facebook.com", "tel:+15551234567", "#",
        "ftp://facebook.com/acme", "facebook.com/acme", "www.instagram.com/acme", "https://facebook.com:notaport/acme",
        "https://www.facebook.com/ac me", "https://x.com/waytoolongusername1234567890", "http://%zz", "https://@/acme",
    ],
)
def test_malformed_links_are_ignored_without_error(href):
    assert classify_social_url(href) is None


def test_malformed_markup_and_non_string_values_do_not_break_parsing():
    page = parse_html('<a href="http://[bad">a</a><a href>b</a><a>c</a><a href="https://x.com/ok">d</a><a href="https://x.com/ok')
    assert [s.url for s in page.social_links] == ["https://x.com/ok"]
    assert classify_social_url(None) is None  # type: ignore[arg-type]


# 7. page without social links
def test_page_without_social_links():
    page = parse_html('<html><body><h1>Hi</h1><p>No socials</p><a href="/about">About</a><a href="https://example.com">E</a></body></html>')
    assert page.social_links == ()
    assert parse_html("").social_links == ()
    assert ParsedPage().social_links == ()


# 8. Persian / mixed-language page
def test_persian_and_mixed_language_page():
    html = (
        '<html lang="fa"><head><meta charset="utf-8"><title>شبکه‌های اجتماعی ما</title></head><body>'
        "<h1>ما را دنبال کنید</h1>"
        '<p><a href="https://www.instagram.com/acme.ir/">اینستاگرام</a> | '
        '<a href="https://t.me/acme">تلگرام</a> | <a href="https://twitter.com/acme_ir?lang=fa">توییتر</a></p>'
        '<p><a href="https://www.linkedin.com/in/%D8%B9%D9%84%DB%8C-%D8%B1%D8%B6%D8%A7%DB%8C%DB%8C">لینکدین</a> '
        '<a href="https://www.youtube.com/@%D9%81%D8%A7%D8%B1%D8%B3%DB%8C">یوتیوب</a> '
        '<a href="https://www.youtube.com/@فارسی">یوتیوب ۲</a> Follow us: '
        '<a href="https://www.tiktok.com/@acme_ir">TikTok</a> <a href="https://example.ir/facebook">فیسبوک</a></p>'
        "</body></html>"
    )
    page = parse_html(html)
    assert page.social_links == (
        SocialLink("instagram", "https://www.instagram.com/acme.ir"),
        SocialLink("linkedin", "https://www.linkedin.com/in/%D8%B9%D9%84%DB%8C-%D8%B1%D8%B6%D8%A7%DB%8C%DB%8C"),
        SocialLink("tiktok", "https://www.tiktok.com/@acme_ir"),
        SocialLink("x", "https://x.com/acme_ir"),
        SocialLink("youtube", "https://www.youtube.com/@%D9%81%D8%A7%D8%B1%D8%B3%DB%8C"),
        SocialLink("youtube", "https://www.youtube.com/@فارسی"),
    )
    assert "اینستاگرام" in page.text  # existing text handling unchanged


# scope / integration
def test_only_visible_anchors_count_not_script_style_or_comments():
    html = (
        '<script>var a = \'<a href="https://x.com/js">\';</script><!-- <a href="https://x.com/commented">x</a> -->'
        '<noscript><a href="https://x.com/noscript">n</a></noscript><a href="https://x.com/real">r</a>'
        '<link rel="me" href="https://x.com/linktag"><meta property="og:url" content="https://x.com/meta">'
    )
    assert socials_of(html) == ("https://x.com/real",)


def socials_of(html: str) -> tuple[str, ...]:
    return tuple(s.url for s in parse_html(html).social_links)


def test_social_links_found_even_past_the_link_cap():
    filler = "".join(f'<a href="/p{i}">x</a>' for i in range(MAX_LINKS_PER_PAGE + 5))
    page = parse_html(filler + '<a href="https://www.instagram.com/late">late</a>')
    assert len(page.links) == MAX_LINKS_PER_PAGE and "https://www.instagram.com/late" not in page.links
    assert [s.url for s in page.social_links] == ["https://www.instagram.com/late"]


def test_links_email_and_phone_are_unchanged_and_to_dict_is_json_ready():
    html = '<p>mail a@example.com call +1 555 123 4567</p><a href="https://x.com/acme?x=1">x</a><a href="mailto:b@example.com">m</a>'
    page = parse_html(html)
    assert page.links == ("https://x.com/acme?x=1", "mailto:b@example.com")  # raw hrefs untouched
    assert page.emails == ("a@example.com", "b@example.com")
    assert [p.number for p in page.phones] == ["+15551234567"]
    data = page.to_dict()
    assert data["social_links"] == [{"platform": "x", "url": "https://x.com/acme"}]
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data


def test_platform_detection_is_extensible_without_touching_the_parser():
    def mastodon(segments, query):
        return "/" + segments[0] if len(segments) == 1 and segments[0].startswith("@") else None

    extra = SocialPlatform("mastodon", "mastodon.social", frozenset({"mastodon.social"}), mastodon)
    assert extract_social_links(["https://mastodon.social/@acme", "https://x.com/acme"], (extra,)) == (
        SocialLink("mastodon", "https://mastodon.social/@acme"),
    )
    assert extract_social_links(["https://mastodon.social/@acme"]) == ()  # default registry unchanged
