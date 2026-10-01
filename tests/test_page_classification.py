"""Phase 7.3.2: deterministic page classification (pure, no network)."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import quote

import pytest

from app.crawler import (
    FetchOutcome, PageCategory, PageClassification, PageResult, classify_page, parse_html, parse_response,
)
from app.crawler.classify import normalize

C = PageCategory


def build(title: str = "", body: str = "", head: str = "") -> object:
    return parse_html(f"<html><head><title>{title}</title>{head}</head><body>{body}</body></html>")


def cat(url: str | None = None, title: str = "", body: str = "", head: str = "") -> PageCategory:
    return classify_page(build(title, body, head), url).category


FILLER = "<p>" + "Lorem ipsum dolor sit amet consectetur. " * 30 + "</p>"


# --------------------------------------------------------------------------- one representative per category
@pytest.mark.parametrize("url", [
    "https://acme.example/", "https://acme.example", "https://acme.example/index.html", "https://acme.example/index.php",
    "https://acme.example/home", "https://acme.example/en/", "https://acme.example/fa", "https://acme.example/fa-IR/",
    "https://acme.example/en-US/index.html", "https://acme.example/default.aspx",
])
def test_homepage_by_url(url):
    r = classify_page(build("Welcome", "<h1>Welcome</h1>"), url)
    assert r.category == C.HOMEPAGE and r.signals == ("url",)


def test_homepage_beats_other_signals():
    assert cat("https://a.example/", "Contact us", "<h1>Contact us</h1>") == C.HOMEPAGE


def test_homepage_not_with_query_string():
    assert cat("https://a.example/?p=123", "Hello") == C.OTHER
    assert cat("https://a.example/?s=contact", "Search") == C.OTHER


def test_homepage_from_canonical_when_no_url():
    page = build("Hi", head='<link rel="canonical" href="https://a.example/">')
    assert classify_page(page).category == C.HOMEPAGE


def test_never_homepage_without_any_url():
    assert cat(None, "Home", "<h1>Home</h1>") == C.OTHER


@pytest.mark.parametrize("url,title,expected", [
    ("https://a.example/about-us", "Acme", C.ABOUT),
    ("https://a.example/about", "", C.ABOUT),
    ("https://a.example/company/our-team", "", C.ABOUT),
    ("https://a.example/contact-us", "", C.CONTACT),
    ("https://a.example/contact.html", "", C.CONTACT),
    ("https://a.example/get-in-touch", "", C.CONTACT),
    ("https://a.example/services", "", C.SERVICES),
    ("https://a.example/services/web-design", "", C.SERVICES),
    ("https://a.example/products", "", C.PRODUCT),
    ("https://a.example/shop/blue-widget", "", C.PRODUCT),
    ("https://a.example/blog", "", C.BLOG),
    ("https://a.example/blog/", "", C.BLOG),
    ("https://a.example/news", "", C.BLOG),
    ("https://a.example/blog/how-to-bake-bread", "", C.ARTICLE),
    ("https://a.example/news/company-wins-award", "", C.ARTICLE),
    ("https://a.example/2024/05/how-to-bake", "", C.ARTICLE),
    ("https://a.example/2024/05", "", C.BLOG),
    ("https://a.example/article/42", "", C.ARTICLE),
    ("https://a.example/portfolio", "", C.PORTFOLIO),
    ("https://a.example/our-work/project-x", "", C.PORTFOLIO),
    ("https://a.example/case-studies", "", C.PORTFOLIO),
    ("https://a.example/careers", "", C.CAREERS),
    ("https://a.example/careers/senior-engineer", "", C.CAREERS),
    ("https://a.example/jobs", "", C.CAREERS),
    ("https://a.example/join-our-team", "", C.CAREERS),
])
def test_url_signals(url, title, expected):
    assert cat(url, title) == expected


@pytest.mark.parametrize("title,h1,expected", [
    ("About Us | Acme", "", C.ABOUT),
    ("Contact Us - Acme", "", C.CONTACT),
    ("Our Services | Acme", "", C.SERVICES),
    ("Products", "", C.PRODUCT),
    ("Blog | Acme", "", C.BLOG),
    ("Portfolio | Acme", "", C.PORTFOLIO),
    ("Careers at Acme", "", C.CAREERS),
    ("Acme", "Contact us", C.CONTACT),
    ("Acme", "Our Portfolio", C.PORTFOLIO),
    ("Acme", "We are hiring", C.CAREERS),
])
def test_title_and_h1_signals_without_url(title, h1, expected):
    body = f"<h1>{h1}</h1>" if h1 else ""
    assert cat(None, title, body) == expected


def test_metadata_and_headings_combine():
    r = classify_page(build("Acme", "<h2>Our services</h2>", '<meta name="description" content="Services for you">'), None)
    assert r.category == C.SERVICES and r.signals == ("heading", "meta") and r.score == 2


def test_text_signals_product_article_contact_structure():
    assert cat(None, "Blue Widget", "<p>Great widget. Add to cart</p>") == C.PRODUCT
    assert cat(None, "Why bread rises", "<p>Posted on May 5. Written by Sam. 5 min read</p>" + FILLER) == C.ARTICLE
    body = "<p>Write to us</p><a href='mailto:hi@acme.example'>hi@acme.example</a><address>1 Main St, Springfield</address>"
    r = classify_page(build("Acme", body), None)
    assert r.category == C.CONTACT and r.signals == ("structure",)
    assert cat(None, "Acme", body + "<p>" + "word " * 1000 + "</p>") == C.OTHER  # a long page is not a contact page


def test_blog_index_by_repeated_read_more():
    body = "".join(f"<article><h3>Post {i}</h3><a href='/p{i}'>Read more</a></article>" for i in range(4))
    assert cat(None, "Acme Blog Posts", body) == C.BLOG


def test_blog_section_vs_article_depends_on_slug():
    assert cat("https://a.example/blog") == C.BLOG
    assert cat("https://a.example/blog/hello") == C.ARTICLE
    assert cat("https://a.example/en/blog/hello") == C.ARTICLE


# --------------------------------------------------------------------------- unknown / weak evidence
def test_unknown_page_is_other():
    r = classify_page(build("Privacy policy", "<h1>Privacy</h1><p>We respect your data.</p>"), "https://a.example/privacy")
    assert r == PageClassification(C.OTHER, 0, (), False)


def test_empty_page_and_empty_url():
    assert classify_page(parse_html(""), "").category == C.OTHER
    assert classify_page(parse_html(""), None).category == C.OTHER
    assert classify_page(parse_html(""), "https://a.example/").category == C.HOMEPAGE


def test_single_weak_signal_is_not_enough():
    assert cat(None, "Acme", "<h2>Contact</h2>") == C.OTHER
    assert cat(None, "Acme", head='<meta name="keywords" content="careers">') == C.OTHER


def test_site_name_in_title_suffix_is_a_weak_signal():
    assert cat("https://a.example/privacy", "Privacy | Acme Services") == C.OTHER
    # Known limitation: a site name placed FIRST in the title counts as page-specific evidence (2 points).
    assert cat("https://a.example/privacy", "Acme Services | Privacy") == C.SERVICES


def test_words_must_match_whole_tokens():
    assert cat("https://a.example/aboutface", "Contactless payments") == C.OTHER
    assert cat("https://a.example/shopify-tips") == C.OTHER


def test_malformed_urls_do_not_raise():
    for url in ("http://[bad", "::::", "  ", "https://", "/about", "about"):
        assert isinstance(classify_page(build("x"), url), PageClassification)
    assert cat("/about") == C.ABOUT


# --------------------------------------------------------------------------- conflicting signals
def test_url_outweighs_title():
    r = classify_page(build("About us"), "https://a.example/contact")
    assert r.category == C.CONTACT and r.score == 4 and not r.ambiguous


def test_tie_is_broken_by_fixed_priority_and_flagged():
    r = classify_page(build("Contact us", "<h1>About us</h1>"), None)  # 2 vs 2
    assert r.category == C.CONTACT and r.ambiguous
    r = classify_page(build("Our services", "<h1>Our products</h1>"), None)
    assert r.category == C.SERVICES and r.ambiguous


def test_conflict_resolved_by_more_evidence():
    r = classify_page(build("Careers", "<h1>Careers</h1><h2>Contact</h2>"), "https://a.example/careers")
    assert r.category == C.CAREERS and not r.ambiguous and r.score == 8


def test_close_runner_up_is_ambiguous():
    r = classify_page(build("Contact", "<h1>About</h1>"), "https://a.example/about")  # about 6, contact 2
    assert r.category == C.ABOUT and not r.ambiguous
    r = classify_page(build("Contact us"), "https://a.example/about-the-company/contact")  # contact 4+2 vs about 3
    assert r.category == C.CONTACT and not r.ambiguous
    r = classify_page(build("Services"), "https://a.example/about")  # 4 vs 2
    assert r.category == C.ABOUT
    r = classify_page(build("About us", "<h1>Services</h1>"), "https://a.example/contact")  # 4 vs 2 vs 2
    assert r.category == C.CONTACT


def test_path_with_two_categories_prefers_last_segment():
    assert cat("https://a.example/services/contact") == C.CONTACT
    assert cat("https://a.example/contact/services") == C.SERVICES


# --------------------------------------------------------------------------- Persian
@pytest.mark.parametrize("slug,expected", [
    ("درباره-ما", C.ABOUT), ("تماس-با-ما", C.CONTACT), ("خدمات", C.SERVICES), ("محصولات", C.PRODUCT),
    ("وبلاگ", C.BLOG), ("نمونه-کارها", C.PORTFOLIO), ("فرصت-های-شغلی", C.CAREERS), ("استخدام", C.CAREERS),
])
def test_persian_slugs_decoded_and_percent_encoded(slug, expected):
    assert cat(f"https://a.example/{slug}") == expected
    assert cat(f"https://a.example/{quote(slug)}") == expected
    assert cat(f"https://a.example/fa/{quote(slug)}/") == expected


def test_persian_article_slug_under_blog():
    assert cat("https://a.example/" + quote("وبلاگ") + "/" + quote("آموزش-پخت-نان")) == C.ARTICLE
    assert cat("https://a.example/" + quote("مقاله") + "/12") == C.ARTICLE


@pytest.mark.parametrize("title,expected", [
    ("درباره ما | شرکت آکمه", C.ABOUT), ("تماس با ما - آکمه", C.CONTACT), ("خدمات ما", C.SERVICES),
    ("محصولات", C.PRODUCT), ("وبلاگ آکمه", C.BLOG), ("نمونه‌کارها", C.PORTFOLIO), ("فرصت‌های شغلی", C.CAREERS),
    ("نمونه کار ها", C.PORTFOLIO),
])
def test_persian_titles_with_zwnj_and_spacing_variants(title, expected):
    assert cat(None, title) == expected


def test_persian_arabic_letter_forms_match():
    assert cat(None, "تماس با ما".replace("ی", "ي")) == C.CONTACT
    assert cat(None, "كاتالوگ") == C.PRODUCT


def test_persian_text_signals():
    assert cat(None, "گوشی", "<p>افزودن به سبد خرید</p>") == C.PRODUCT
    assert cat(None, "نان", "<p>منتشر شده در ۱۴۰۲. نویسنده: سارا</p>" + FILLER) == C.ARTICLE


def test_persian_page_with_english_url_and_vice_versa():
    assert cat("https://a.example/contact", "تماس با ما") == C.CONTACT
    r = classify_page(build("About us"), "https://a.example/" + quote("درباره-ما"))
    assert r.category == C.ABOUT and r.score == 6


# --------------------------------------------------------------------------- mixed language
def test_mixed_language_page():
    r = classify_page(build("تماس با ما | Contact Us", "<h1>تماس با ما / Contact us</h1>"), None)
    assert r.category == C.CONTACT and r.score == 5 and r.signals == ("title", "title_suffix", "h1") and not r.ambiguous


def test_mixed_language_conflict_is_deterministic():
    r = classify_page(build("درباره ما | Services"), None)
    assert r.category == C.ABOUT and r.score == 2 and not r.ambiguous  # services: 1 point (suffix) is below the threshold
    r = classify_page(build("درباره ما", "<h1>Our services</h1>"), None)
    assert r.category == C.ABOUT and r.ambiguous


def test_mixed_language_url_and_content():
    assert cat("https://a.example/en/" + quote("خدمات"), "Our Services") == C.SERVICES


# --------------------------------------------------------------------------- integration with ParsedPage
def result(html: str, url: str = "https://a.example/contact") -> PageResult:
    body = html.encode("utf-8")
    return PageResult(url=url, final_url=url, outcome=FetchOutcome.OK, status_code=200, content_type="text/html",
                      charset="utf-8", body=body, size=len(body))


def test_parsed_page_carries_classification_and_to_dict():
    page = parse_html("<html><head><link rel='canonical' href='https://a.example/about-us'></head><body>x</body></html>")
    assert page.classification.category == C.ABOUT
    assert page.to_dict()["classification"] == {"category": "about", "score": 4, "signals": ["url"], "ambiguous": False}


def test_parse_response_uses_final_url():
    res = result("<title>Hi</title><p>x</p>", "https://a.example/careers")
    assert parse_response(replace(res, url="https://a.example/old")).classification.category == C.CAREERS
    assert parse_response(result("<p>x</p>", "https://a.example/")).classification.category == C.HOMEPAGE


def test_default_is_other_and_other_fields_unaffected():
    assert parse_html("").classification == PageClassification()
    page = parse_response(result("<html lang='fa'><title>T</title><h1>H</h1><a href='/x'>l</a><p>سلام دنیا خوب</p>"))
    assert page.title == "T" and page.language.language == "fa" and page.encoding == "utf-8"
    assert [h.text for h in page.headings] == ["H"] and page.links == ("/x",)


def test_classification_is_deterministic():
    html = "<title>Contact us | About us</title><h1>Services</h1><h2>Careers</h2><p>Add to cart</p>"
    runs = {repr(classify_page(parse_html(html), "https://a.example/x/y")) for _ in range(20)}
    assert len(runs) == 1
    assert repr(classify_page(parse_html(html), "https://a.example/x/y")) == repr(classify_page(parse_html(html), "https://a.example/x/y"))


def test_classification_does_not_exclude_or_modify_anything():
    page = parse_html("<title>Privacy</title><a href='/a'>a</a><a href='/b'>b</a>")
    assert page.links == ("/a", "/b")  # links of every page, whatever its category, stay available


def test_normalize_helper():
    assert normalize("  Contact_Us!! ") == "contact us"
    assert normalize("نمونه\u200cکار\u200cها") == "نمونه کارها"
    assert normalize("تَمَاس") == "تماس"
