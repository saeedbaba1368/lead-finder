from app.discovery.sitemap import SitemapKind, parse_sitemap
from app.discovery.sitemap import SitemapParseStatus as S

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def urlset(*locs: str, ns: str = NS) -> bytes:
    body = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    xmlns = f' xmlns="{ns}"' if ns else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset{xmlns}>{body}</urlset>'.encode()


class TestSitemapParsing:
    def test_valid_urlset(self):
        r = parse_sitemap(urlset("https://example.com/page-1"))
        assert r.status is S.OK and r.locs == ["https://example.com/page-1"]

    def test_multiple_urls_and_no_namespace(self):
        r = parse_sitemap(urlset("https://example.com/a", "https://example.com/b", ns=""))
        assert r.locs == ["https://example.com/a", "https://example.com/b"]

    def test_duplicates_are_kept_by_parser(self):
        assert len(parse_sitemap(urlset("https://e.com/a", "https://e.com/a")).locs) == 2

    def test_empty_loc_is_counted_and_skipped(self):
        r = parse_sitemap(urlset("", "  ", "https://example.com/a"))
        assert r.locs == ["https://example.com/a"] and r.empty_locs == 2

    def test_whitespace_around_loc_is_stripped(self):
        assert parse_sitemap(urlset("\n  https://example.com/a \n")).locs == ["https://example.com/a"]

    def test_standard_namespaces_and_extensions(self):
        data = (
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            b'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1" '
            b'xmlns:xhtml="http://www.w3.org/1999/xhtml">'
            b"<url><loc>https://example.com/a</loc><lastmod>2026-01-01</lastmod>"
            b"<changefreq>daily</changefreq><priority>0.8</priority>"
            b"<image:image><image:loc>https://example.com/i.png</image:loc></image:image>"
            b'<xhtml:link rel="alternate" hreflang="de" href="https://example.com/de/a"/>'
            b"<custom>ignored</custom></url></urlset>"
        )
        r = parse_sitemap(data)
        assert r.status is S.OK and r.locs == ["https://example.com/a"]  # image:loc not a page

    def test_unknown_top_level_elements_ignored(self):
        data = b"<urlset><note>hi</note><url><loc>https://e.com/a</loc></url><x/></urlset>"
        assert parse_sitemap(data).locs == ["https://e.com/a"]

    def test_url_without_loc(self):
        assert parse_sitemap(b"<urlset><url><lastmod>x</lastmod></url></urlset>").locs == []

    def test_malformed_xml(self):
        for data in (b"<urlset><url><loc>x</url>", b"not xml at all", b'<?xml version="1.0" encoding="no-such-enc"?><urlset/>'):
            assert parse_sitemap(data).status is S.MALFORMED

    def test_empty(self):
        assert parse_sitemap(b"").status is S.EMPTY
        assert parse_sitemap(b"  \n ").status is S.EMPTY

    def test_unexpected_structure(self):
        assert parse_sitemap(b"<html><body/></html>").status is S.UNSUPPORTED
        assert parse_sitemap(b"<rss><channel/></rss>").status is S.UNSUPPORTED

    def test_sitemapindex_locs_extracted(self):
        data = (
            b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<sitemap><loc>https://e.com/products.xml</loc><lastmod>2026-01-01</lastmod></sitemap>"
            b"<sitemap><loc>https://e.com/blog.xml</loc></sitemap></sitemapindex>"
        )
        r = parse_sitemap(data)
        assert r.status is S.OK and r.kind is SitemapKind.INDEX
        assert r.locs == ["https://e.com/products.xml", "https://e.com/blog.xml"]

    def test_kind_comes_from_root_not_filename(self):
        assert parse_sitemap(urlset("https://e.com/a")).kind is SitemapKind.URLSET

    def test_index_missing_and_empty_loc(self):
        data = (
            b"<sitemapindex><sitemap><lastmod>x</lastmod></sitemap><sitemap><loc> </loc></sitemap>"
            b"<sitemap><loc>https://e.com/ok.xml</loc></sitemap><url><loc>https://e.com/p</loc></url>"
            b"</sitemapindex>"
        )
        r = parse_sitemap(data)
        assert r.locs == ["https://e.com/ok.xml"] and r.missing_locs == 1 and r.empty_locs == 1

    def test_urlset_ignores_sitemap_entries_and_vice_versa(self):
        r = parse_sitemap(b"<urlset><sitemap><loc>https://e.com/s.xml</loc></sitemap></urlset>")
        assert r.status is S.OK and r.locs == []

    def test_url_without_loc_counted(self):
        r = parse_sitemap(b"<urlset><url><lastmod>x</lastmod></url></urlset>")
        assert r.missing_locs == 1

    def test_billion_laughs_rejected(self):
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<urlset><url><loc>&b;</loc></url></urlset>"
        )
        assert parse_sitemap(bomb).status is S.UNSAFE

    def test_external_entity_rejected(self):
        xxe = (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b"<urlset><url><loc>&e;</loc></url></urlset>"
        )
        assert parse_sitemap(xxe).status is S.UNSAFE
        assert parse_sitemap(b"<!doctype html><urlset/>").status is S.UNSAFE

    def test_utf16_doctype_rejected(self):
        data = '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY e "x">]><urlset/>'.encode("utf-16")
        assert parse_sitemap(data).status in (S.UNSAFE, S.MALFORMED)

    def test_too_large(self):
        assert parse_sitemap(urlset("https://e.com/a"), max_bytes=10).status is S.TOO_LARGE

    def test_binary_garbage_never_raises(self):
        assert parse_sitemap(bytes(range(256)) * 10).status is S.MALFORMED

    def test_deeply_nested_never_raises(self):
        data = b"<urlset>" + b"<a>" * 5000 + b"</a>" * 5000 + b"</urlset>"
        assert parse_sitemap(data).status in (S.OK, S.MALFORMED)

    def test_loc_cap(self):
        from app.discovery import sitemap as sm

        old = sm.MAX_LOCS
        sm.MAX_LOCS = 2
        try:
            r = parse_sitemap(urlset("https://e.com/a", "https://e.com/b", "https://e.com/c"))
        finally:
            sm.MAX_LOCS = old
        assert len(r.locs) == 2 and r.truncated
