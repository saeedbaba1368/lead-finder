"""Phase 12.1: website metadata enrichment (parser fields, `WebsiteMetadata`, merge, storage, collector).

No network. The database tests use the in-memory SQLite `session` fixture.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.crawler import CrawledPage, FetchOutcome, PageResult, parse_html
from app.leads import BusinessLead, LeadCollector, LeadDataError, WebsiteMetadata, merge_metadata
from app.repositories import CrawlRepository, DomainRepository, LeadRepository

URL = "https://www.acme.example/about/"
SEEN = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

FULL = """<html lang="en"><head>
<title>  Acme   Plumbing </title>
<meta name="description" content="Family plumbers.">
<meta name="keywords" content="plumbing, boilers">
<link rel="canonical" href="/about">
<link rel="shortcut icon" href="/static/fav.ico">
<link rel="icon" href="/other.png">
<meta property="og:title" content="Acme OG">
<meta property="og:description" content="OG description">
<meta property="og:image" content="/img/og.png">
<meta property="og:url" content="https://acme.example/about">
<meta name="twitter:card" content="Summary_Large_Image">
<meta name="twitter:title" content="Acme Tweet">
<meta name="twitter:description" content="Tweet description">
<meta name="twitter:image" content="https://cdn.acme.example/tw.png">
</head><body><h1>Acme</h1></body></html>"""


def meta(html: str = FULL, url: str = URL) -> WebsiteMetadata:
    return WebsiteMetadata.from_parsed_page(parse_html(html, url), source_url=url)


# ------------------------------------------------------------------ parser
def test_parser_reads_the_new_raw_fields_first_occurrence_wins():
    m = parse_html(FULL, URL).metadata
    assert m.og_image == "/img/og.png" and m.favicon_url == "/static/fav.ico"
    assert (m.twitter_card, m.twitter_title) == ("Summary_Large_Image", "Acme Tweet")
    assert (m.twitter_description, m.twitter_image) == ("Tweet description", "https://cdn.acme.example/tw.png")
    assert parse_html(FULL, URL).to_dict()["metadata"]["favicon_url"] == "/static/fav.ico"


def test_missing_new_fields_stay_none():
    m = parse_html("<html><head><title>x</title></head></html>").metadata
    assert (m.og_image, m.favicon_url, m.twitter_card, m.twitter_title, m.twitter_image) == (None,) * 5


def test_apple_touch_icon_is_not_a_favicon_and_blank_href_is_ignored():
    html = '<head><link rel="apple-touch-icon" href="/a.png"><link rel="icon" href="  "></head>'
    assert parse_html(html).metadata.favicon_url is None


# ------------------------------------------------------------------ WebsiteMetadata
def test_all_fields_are_extracted_cleaned_and_urls_resolved_and_normalised():
    assert meta().to_dict() == {
        "title": "Acme Plumbing", "description": "Family plumbers.", "keywords": "plumbing, boilers",
        "favicon_url": "https://www.acme.example/static/fav.ico", "canonical_url": "https://www.acme.example/about",
        "og_title": "Acme OG", "og_description": "OG description", "og_image": "https://www.acme.example/img/og.png",
        "og_url": "https://acme.example/about", "twitter_card": "summary_large_image", "twitter_title": "Acme Tweet",
        "twitter_description": "Tweet description", "twitter_image": "https://cdn.acme.example/tw.png",
    }


def test_missing_metadata_is_safely_optional():
    empty = meta("<html><body>nothing</body></html>")
    assert empty.is_empty and empty == WebsiteMetadata()
    only_title = meta("<html><head><title>Hi</title></head></html>")
    assert only_title.title == "Hi" and only_title.description is None and not only_title.is_empty


@pytest.mark.parametrize("href", ["data:image/png;base64,AAAA", "javascript:void(0)", "http://", "mailto:a@b.example"])
def test_unusable_urls_become_none_instead_of_raising(href):
    html = f'<head><link rel="icon" href="{href}"><meta property="og:image" content="{href}"></head>'
    m = meta(html)
    assert m.favicon_url is None and m.og_image is None


def test_url_normalisation_matches_the_url_engine():
    html = '<head><link rel="icon" href="HTTPS://WWW.Acme.Example:443/Fav.ico#x"></head>'
    assert meta(html).favicon_url == "https://www.acme.example/Fav.ico"


def test_persian_text_survives():
    assert meta("<head><title>آکمه\u200cلوله</title></head>").title == "آکمه\u200cلوله"


def test_extraction_is_deterministic_and_round_trips_through_json():
    one, two = meta(), meta()
    assert one == two and one.to_dict() == two.to_dict()
    assert WebsiteMetadata.from_dict(json.loads(json.dumps(one.to_dict()))) == one


def test_from_dict_rejects_unknown_keys_and_wrong_types():
    with pytest.raises(LeadDataError):
        WebsiteMetadata.from_dict({"title": "x", "seo_score": 5})
    with pytest.raises(LeadDataError):
        WebsiteMetadata.from_dict({"title": 5})
    assert WebsiteMetadata.from_dict({}).is_empty


# ------------------------------------------------------------------ merge
def test_merge_keeps_stored_values_and_fills_only_missing_ones():
    stored = WebsiteMetadata(title="Home", description=None, og_image="https://acme.example/a.png")
    page = WebsiteMetadata(title="About", description="About us", og_image="https://acme.example/b.png", twitter_card="summary")
    merged = merge_metadata(stored, page)
    assert (merged.title, merged.description) == ("Home", "About us")
    assert merged.og_image == "https://acme.example/a.png" and merged.twitter_card == "summary"


def test_merge_is_idempotent_and_handles_none_and_empty():
    one = meta()
    assert merge_metadata(one, one) == one and merge_metadata(merge_metadata(one, one), one) == one
    assert merge_metadata(None, None) is None and merge_metadata(WebsiteMetadata(), WebsiteMetadata()) is None
    assert merge_metadata(None, one) == one and merge_metadata(one, None) == one and merge_metadata(one, WebsiteMetadata()) == one


# ------------------------------------------------------------------ storage
def lead(url: str = "https://acme.example/") -> BusinessLead:
    return BusinessLead(website=url, first_seen=SEEN, last_seen=SEEN)


def test_repository_stores_and_loads_metadata_and_never_duplicates(session):
    repo = LeadRepository(session)
    repo.merge(lead())
    assert repo.load_metadata("acme.example") is None  # no metadata yet: optional
    assert repo.merge_metadata("acme.example", WebsiteMetadata(title="Home", description="D")) is True
    assert repo.merge_metadata("acme.example", WebsiteMetadata(title="Home", description="D")) is False  # same data again
    assert repo.merge_metadata("acme.example", WebsiteMetadata(title="Other", keywords="k")) is True
    assert repo.load_metadata("acme.example") == WebsiteMetadata(title="Home", description="D", keywords="k")
    assert len(repo.load_all()) == 1


def test_repository_without_a_lead_or_metadata_stores_nothing(session):
    repo = LeadRepository(session)
    assert repo.merge_metadata("nobody.example", WebsiteMetadata(title="x")) is False
    repo.merge(lead())
    assert repo.merge_metadata("acme.example", None) is False
    assert repo.merge_metadata("acme.example", WebsiteMetadata()) is False
    assert repo.load_metadata("acme.example") is None


def test_saving_and_merging_the_lead_keeps_the_stored_metadata_and_the_exports_are_unchanged(session):
    repo = LeadRepository(session)
    repo.merge(lead())
    repo.merge_metadata("acme.example", WebsiteMetadata(title="Home"))
    repo.merge(BusinessLead(website="https://acme.example/", business_name="Acme", last_seen=SEEN))
    repo.save(lead())
    assert repo.load_metadata("acme.example") == WebsiteMetadata(title="Home")
    assert "website_metadata" not in repo.load("acme.example").to_dict()  # BusinessLead / exports unchanged


# ------------------------------------------------------------------ collector
def new_crawl(session) -> int:
    row, _ = DomainRepository(session).get_or_create("acme.example")
    return CrawlRepository(session).create(row.id, seed_url="https://acme.example/").id


def crawled(url: str, html: str) -> CrawledPage:
    res = PageResult(url, url, FetchOutcome.OK, 200, "text/html", "utf-8", {}, 10, b"x")
    return CrawledPage(url, 0, None, res, parsed=parse_html(html, url))


def test_collector_stores_metadata_once_for_many_pages_of_one_site(session):
    repo = LeadRepository(session)
    collect, crawl_id = LeadCollector(repo, now=lambda: SEEN), new_crawl(session)
    home = '<head><title>Acme</title><link rel="icon" href="/f.ico"><meta property="og:image" content="/og.png"></head>'
    about = '<head><title>About | Acme</title><meta name="description" content="About us"><link rel="icon" href="/g.ico"></head>'
    collect(crawled("https://acme.example/", home), crawl_id)
    collect(crawled("https://acme.example/about", about), crawl_id)
    collect(crawled("https://acme.example/", home), crawl_id)  # a repeated page changes nothing
    stored = repo.load_metadata("acme.example")
    assert stored.title == "Acme" and stored.favicon_url == "https://acme.example/f.ico"
    assert stored.og_image == "https://acme.example/og.png" and stored.description == "About us"
    assert len(repo.load_all()) == 1


def test_collector_without_any_metadata_stores_none(session):
    repo = LeadRepository(session)
    page = crawled("https://acme.example/", "<html><body><h1>Acme</h1></body></html>")
    LeadCollector(repo, now=lambda: SEEN)(page, new_crawl(session))
    assert repo.load("acme.example") is not None and repo.load_metadata("acme.example") is None
