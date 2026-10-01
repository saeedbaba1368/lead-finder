"""Phase 7.2.4: JSON-LD and <address> extraction (pure parsing, no network)."""

from __future__ import annotations

import json

import pytest

from app.crawler import Address, ParsedPage, PhoneNumber, SocialLink, StructuredBusiness, parse_html
from app.crawler.structured import parse_jsonld


def ld(data: object, *, raw: bool = False) -> str:
    body = data if raw else json.dumps(data, ensure_ascii=False)
    return f'<script type="application/ld+json">{body}</script>'


def page_with(*blocks: str, body: str = "<p>x</p>") -> ParsedPage:
    return parse_html(f"<html><head><title>T</title>{''.join(blocks)}</head><body>{body}</body></html>")


ORG = {
    "@context": "https://schema.org",
    "@type": "Organization",
    "name": "Acme   Corp",
    "url": "https://acme.example/",
    "description": "We build\n anvils.",
    "telephone": "+1 555 123 4567",
    "email": "mailto:Hello@Acme.example",
    "logo": "https://acme.example/logo.png",
    "sameAs": ["https://www.facebook.com/acme", "https://twitter.com/acme", "https://en.wikipedia.org/wiki/Acme"],
    "address": {
        "@type": "PostalAddress", "streetAddress": "1 Main St", "addressLocality": "Springfield",
        "addressRegion": "IL", "postalCode": "62701", "addressCountry": "US",
    },
}


# 1. Organization JSON-LD
def test_organization_jsonld_all_fields():
    page = page_with(ld(ORG))
    assert page.structured_data == (
        StructuredBusiness(
            type="Organization", name="Acme Corp", url="https://acme.example/", description="We build anvils.",
            telephones=(PhoneNumber("+15551234567", "+1 555 123 4567"),), emails=("hello@acme.example",),
            addresses=(Address("1 Main St", "Springfield", "IL", "62701", "US", "1 Main St, Springfield, IL, 62701, US"),),
            same_as=("https://en.wikipedia.org/wiki/Acme", "https://twitter.com/acme", "https://www.facebook.com/acme"),
            social_links=(SocialLink("facebook", "https://www.facebook.com/acme"), SocialLink("x", "https://x.com/acme")),
            logo="https://acme.example/logo.png",
        ),
    )


@pytest.mark.parametrize("kind", ["Organization", "LocalBusiness", "ProfessionalService", "Corporation", "Person"])
def test_each_recognised_type(kind):
    page = page_with(ld({"@type": kind, "name": "N"}))
    assert [(e.type, e.name) for e in page.structured_data] == [(kind, "N")]


def test_type_variants_and_unrecognised_types():
    for kind in ("schema:Organization", "https://schema.org/Organization", "organization", ["Thing", "Organization"]):
        assert [e.type for e in page_with(ld({"@type": kind, "name": "N"})).structured_data] == ["Organization"]
    assert [e.type for e in page_with(ld({"@type": ["LocalBusiness", "ProfessionalService"], "name": "N"})).structured_data] == ["ProfessionalService"]
    ignored = [{"@type": "WebSite", "name": "S"}, {"@type": "Product", "name": "P"}, {"@type": "Article", "name": "A"}, {"name": "no type"}]
    assert page_with(*(ld(item) for item in ignored)).structured_data == ()


# 2. LocalBusiness JSON-LD (+ contactPoint, logo object)
def test_local_business_jsonld():
    data = {
        "@context": "https://schema.org", "@type": "LocalBusiness", "name": "Joe's Plumbing",
        "image": "https://x.example/i.jpg", "priceRange": "$$", "openingHours": "Mo-Fr 09:00-17:00",
        "logo": {"@type": "ImageObject", "url": "https://joes.example/logo.svg"},
        "address": {"@type": "PostalAddress", "streetAddress": "9 Pipe Rd", "addressLocality": "Leeds", "postalCode": "LS1 4AB", "addressCountry": {"@type": "Country", "name": "UK"}},
        "contactPoint": [{"@type": "ContactPoint", "telephone": "+44 113 496 0000", "email": "support@joes.example"}],
        "telephone": "0113 496 0001",
    }
    [entity] = page_with(ld(data)).structured_data
    assert entity.type == "LocalBusiness" and entity.name == "Joe's Plumbing"
    assert entity.logo == "https://joes.example/logo.svg"
    assert [p.number for p in entity.telephones] == ["+441134960000", "01134960001"]  # sorted by normalised number
    assert entity.emails == ("support@joes.example",)
    assert entity.addresses == (Address("9 Pipe Rd", "Leeds", None, "LS1 4AB", "UK", "9 Pipe Rd, Leeds, LS1 4AB, UK"),)


# 3. JSON-LD arrays, @graph
def test_top_level_array_and_graph_and_nested_arrays():
    array = ld([{"@type": "Organization", "name": "B Org"}, {"@type": "Person", "name": "A Person"}, {"@type": "WebSite", "name": "S"}])
    graph = ld({"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, {"@type": "LocalBusiness", "name": "C Biz"}, [{"@type": "Corporation", "name": "D Corp"}]]})
    page = page_with(array, graph)
    assert [(e.type, e.name) for e in page.structured_data] == [
        ("Corporation", "D Corp"), ("LocalBusiness", "C Biz"), ("Organization", "B Org"), ("Person", "A Person"),
    ]


def test_order_of_blocks_does_not_change_the_result():
    blocks = [ld({"@type": "Organization", "name": n}) for n in ("Zed", "Alpha", "Mid")]
    assert page_with(*blocks).structured_data == page_with(*reversed(blocks)).structured_data
    assert [e.name for e in page_with(*blocks).structured_data] == ["Alpha", "Mid", "Zed"]


# 4. malformed JSON-LD never crashes anything
@pytest.mark.parametrize(
    "body",
    [
        "{not json}", "", "   ", '{"@type": "Organization", "name": "Cut', '{"@type": "Organization", "name": "X",}', "[1, 2,",
        "null", "42", '"just a string"', "true", "[[[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]]]", "[" * 200000, '{"@type": 5, "name": 3}',
        '{"@type": "Organization", "name": {"a": {"b": 1}}, "telephone": {"x": 1}, "address": 12, "sameAs": 7, "logo": [], "email": [null, true]}',
        "<<<>>>", "\x00\x01",
    ],
)
def test_malformed_jsonld_is_ignored_and_page_still_parsed(body):
    page = parse_html(f'<html><head><title>Still here</title>{ld(body, raw=True)}</head><body><h1>H</h1><p>text</p></body></html>')
    assert page.structured_data == ()
    assert page.title == "Still here" and page.headings[0].text == "H" and page.text == "H\ntext"


def test_valid_block_survives_a_malformed_neighbour_and_unclosed_script():
    html = ld("{bad", raw=True) + ld({"@type": "Organization", "name": "Good"}) + '<script type="application/ld+json">{"@type":"Organization","name":"Cut'
    assert [e.name for e in parse_html(html).structured_data] == ["Good"]


def test_html_comment_and_cdata_wrappers_inside_jsonld_are_tolerated():
    body = '<!-- {"@type":"Organization","name":"Wrapped"} -->'
    assert [e.name for e in page_with(ld(body, raw=True)).structured_data] == ["Wrapped"]
    assert [e.name for e in page_with(ld('//<![CDATA[\n{"@type":"Person","name":"C"}\n//]]>', raw=True)).structured_data] == []  # not valid JSON: skipped
    assert [e.name for e in page_with(ld('<![CDATA[{"@type":"Person","name":"C"}]]>', raw=True)).structured_data] == ["C"]


def test_parse_jsonld_itself_never_raises_and_empty_entities_are_dropped():
    assert parse_jsonld(["{bad", "[]", "{}", '{"@type": "Organization"}', '{"@type": "Person", "name": ""}']) == ()
    assert parse_jsonld([]) == ()


# 5. Schema.org address
def test_schema_address_shapes():
    def addr(value):
        [entity] = page_with(ld({"@type": "Organization", "name": "N", "address": value})).structured_data
        return entity.addresses

    assert addr({"streetAddress": "1 A St", "addressLocality": "X", "addressRegion": "Y", "postalCode": 12345, "addressCountry": "DE"}) == (
        Address("1 A St", "X", "Y", "12345", "DE", "1 A St, X, Y, 12345, DE"),
    )
    assert addr("12 Free Text Rd, Town 99999") == (Address(formatted="12 Free Text Rd, Town 99999"),)
    assert addr([{"streetAddress": "B"}, {"streetAddress": "A"}, "  "]) == (Address("A", None, None, None, None, "A"), Address("B", None, None, None, None, "B"))
    assert addr({"@type": "PostalAddress"}) == ()
    assert addr({"@id": "#addr"}) == ()


def test_page_level_addresses_include_schema_addresses():
    page = page_with(ld(ORG))
    assert [a.formatted for a in page.addresses] == ["1 Main St, Springfield, IL, 62701, US"]


# 6. <address> element
def test_address_element_text():
    page = parse_html("<footer><address>Acme Inc.<br>1 Main St,<br/>  Springfield,   IL 62701 <br>USA</address></footer>")
    assert page.addresses == (Address(formatted="Acme Inc., 1 Main St, Springfield, IL 62701, USA"),)


def test_address_element_with_microdata_fields():
    html = (
        '<address itemscope itemtype="https://schema.org/PostalAddress"><span itemprop="streetAddress">221B Baker St</span>, '
        '<span itemprop="addressLocality">London</span> <span itemprop="postalCode">NW1 6XE</span>'
        '<meta itemprop="addressCountry" content="GB"></address>'
    )
    assert parse_html(html).addresses == (Address("221B Baker St", "London", None, "NW1 6XE", "GB", "221B Baker St, London NW1 6XE"),)


def test_address_element_edge_cases():
    assert parse_html("<address></address><address>  <br> </address>").addresses == ()
    assert parse_html("<script><address>js addr</address></script><template><address>t</address></template>").addresses == ()
    assert parse_html("<address>too long " + "x" * 600 + "</address>").addresses == ()
    assert parse_html("<address>Unclosed street 5<p>after").addresses[0].formatted.startswith("Unclosed street 5")
    page = parse_html("<address>Mail: info@example.com, tel +1 555 123 4567</address>")
    assert page.emails == ("info@example.com",) and [p.number for p in page.phones] == ["+15551234567"]  # 7.2.1/7.2.2 unchanged


# 7. partial address
def test_partial_addresses():
    [entity] = page_with(ld({"@type": "LocalBusiness", "name": "N", "address": {"addressLocality": "Paris", "addressCountry": "FR"}})).structured_data
    assert entity.addresses == (Address(None, "Paris", None, None, "FR", "Paris, FR"),)
    assert parse_html("<address>Somewhere in town</address>").addresses == (Address(formatted="Somewhere in town"),)


# 8. sameAs / social data
def test_sameas_populates_social_links_but_not_page_level_social_links():
    page = page_with(ld(ORG), body='<a href="https://www.instagram.com/acme_page">ig</a>')
    [entity] = page.structured_data
    assert [s.platform for s in entity.social_links] == ["facebook", "x"]
    assert page.social_links == (SocialLink("instagram", "https://www.instagram.com/acme_page"),)  # anchors only, as in 7.2.3


def test_sameas_as_string_single_value_and_junk_values():
    [entity] = page_with(ld({"@type": "Organization", "name": "N", "sameAs": "https://www.youtube.com/@acme"})).structured_data
    assert entity.same_as == ("https://www.youtube.com/@acme",) and entity.social_links == (SocialLink("youtube", "https://www.youtube.com/@acme"),)
    [entity] = page_with(ld({"@type": "Organization", "name": "N", "sameAs": ["", "not a url", None, 5, "https://www.facebook.com/sharer/sharer.php?u=x", "ftp://x.example/a"]})).structured_data
    assert entity.same_as == ("https://www.facebook.com/sharer/sharer.php?u=x",) and entity.social_links == ()


# 9. duplicate structured data
def test_identical_blocks_collapse():
    page = page_with(ld(ORG), ld(ORG), ld([ORG, ORG]), ld({"@graph": [ORG]}))
    assert len(page.structured_data) == 1 and len(page.addresses) == 1


def test_same_entity_with_different_detail_is_merged():
    first = {"@type": "Organization", "name": "Acme", "url": "https://acme.example", "telephone": "+1 555 123 4567"}
    second = {"@type": "Organization", "name": "ACME", "url": "https://acme.example/", "email": "a@acme.example", "telephone": "+1-555-123-4567", "sameAs": ["https://x.com/acme"]}
    [entity] = page_with(ld(first), ld(second)).structured_data
    assert entity.name == "Acme" and [p.number for p in entity.telephones] == ["+15551234567"]
    assert entity.emails == ("a@acme.example",) and entity.social_links == (SocialLink("x", "https://x.com/acme"),)


def test_different_entities_are_not_merged():
    page = page_with(ld({"@type": "Organization", "name": "Acme"}), ld({"@type": "LocalBusiness", "name": "Acme"}), ld({"@type": "Organization", "name": "Other"}))
    assert [(e.type, e.name) for e in page.structured_data] == [("LocalBusiness", "Acme"), ("Organization", "Acme"), ("Organization", "Other")]


def test_json_ld_address_and_address_element_are_deduplicated_when_identical():
    page = page_with(ld({"@type": "Organization", "name": "N", "address": "1 Main St, Springfield"}), body="<address>1 Main St, Springfield</address><address>1 MAIN ST, SPRINGFIELD</address>")
    assert page.addresses == (Address(formatted="1 Main St, Springfield"),)


# 10. Persian / mixed-language
def test_persian_and_mixed_language_structured_data():
    data = {
        "@context": "https://schema.org", "@type": "LocalBusiness", "name": "شرکت نمونه (Sample Co)",
        "description": "ارائه خدمات  حرفه‌ای",
        "telephone": "۰۲۱-۱۲۳۴۵۶۷۸", "email": "info@sample.ir",
        "address": {"streetAddress": "خیابان ولیعصر، پلاک ۱۲", "addressLocality": "تهران", "addressRegion": "تهران", "postalCode": "۱۲۳۴۵-۶۷۸۹۰", "addressCountry": "IR"},
        "sameAs": ["https://www.instagram.com/sample.ir", "https://www.linkedin.com/company/sample-ir"],
    }
    for ensure_ascii in (False, True):  # raw Persian and \uXXXX-escaped JSON
        html = (
            '<html lang="fa"><head><meta charset="utf-8"><title>تماس</title>'
            f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=ensure_ascii)}</script></head>'
            "<body><h1>تماس با ما</h1><address>تهران، خیابان ولیعصر،<br>پلاک ۱۲</address></body></html>"
        )
        page = parse_html(html)
        [entity] = page.structured_data
        assert entity.name == "شرکت نمونه (Sample Co)" and entity.description == "ارائه خدمات حرفه‌ای"
        assert entity.telephones == (PhoneNumber("02112345678", "۰۲۱-۱۲۳۴۵۶۷۸"),)
        assert entity.addresses[0].city == "تهران" and entity.addresses[0].street == "خیابان ولیعصر، پلاک ۱۲" and entity.addresses[0].postal_code == "۱۲۳۴۵-۶۷۸۹۰"
        assert [s.platform for s in entity.social_links] == ["instagram", "linkedin"]
        assert Address(formatted="تهران، خیابان ولیعصر, پلاک ۱۲") in page.addresses  # lines are joined with ", "
        assert len(page.addresses) == 2 and page.text == "تماس با ما\nتهران، خیابان ولیعصر،\nپلاک ۱۲"


# 11. page without structured data
def test_page_without_structured_data():
    page = parse_html("<html><body><h1>Hi</h1><p>Nothing here</p><script>var a = 1;</script></body></html>")
    assert page.structured_data == () and page.addresses == ()
    assert parse_html("").structured_data == () and ParsedPage().addresses == ()


# scope / integration
def test_only_real_ld_json_scripts_count():
    body = '{"@type":"Organization","name":"X"}'
    for html in (
        f'<script type="text/javascript">{body}</script>', f'<script>{body}</script>', f'<script type="application/json">{body}</script>',
        f"<!-- {ld(body, raw=True)} -->", f"<noscript>{ld(body, raw=True)}</noscript>", f"<template>{ld(body, raw=True)}</template>",
        f'<div itemscope itemtype="https://schema.org/Organization"><span itemprop="name">X</span></div>',
    ):
        assert parse_html(html).structured_data == (), html
    assert len(parse_html(f'<script type=" Application/LD+JSON ; charset=utf-8">{body}</script>').structured_data) == 1
    assert len(parse_html(f"<script type='application/ld+json'>{body}</script>").structured_data) == 1


def test_existing_fields_are_untouched_by_structured_data():
    html = ld({"@type": "Organization", "name": "N", "telephone": "+1 555 123 4567", "email": "a@example.com", "sameAs": ["https://x.com/acme"]}) + "<p>visible</p>"
    page = parse_html(html)
    assert page.text == "visible"  # JSON-LD is not visible text
    assert page.emails == () and page.phones == () and page.social_links == () and page.links == ()
    assert len(page.structured_data) == 1


def test_to_dict_is_json_ready():
    page = page_with(ld(ORG), body="<address>Somewhere 1</address>")
    data = page.to_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data
    assert data["structured_data"][0]["name"] == "Acme Corp" and data["structured_data"][0]["telephones"][0]["number"] == "+15551234567"
    assert {a["formatted"] for a in data["addresses"]} == {"1 Main St, Springfield, IL, 62701, US", "Somewhere 1"}
    assert set(data["addresses"][0]) == {"street", "city", "region", "postal_code", "country", "formatted"}


def test_entity_and_block_caps_keep_the_parse_bounded():
    many = ld([{"@type": "Person", "name": f"P{i:04d}"} for i in range(300)])
    assert len(page_with(many).structured_data) == 100
    assert len(page_with(*[ld({"@type": "Person", "name": f"Q{i}"}) for i in range(80)]).structured_data) == 50


def test_parse_jsonld_handles_absurd_nesting_without_the_outer_guard():
    assert parse_jsonld(["[" * 200000, '{"a":' * 50000]) == ()
