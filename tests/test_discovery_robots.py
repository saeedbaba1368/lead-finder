from app.discovery.robots import parse_robots


class TestRobotsParsing:
    def test_valid_robots_extracts_groups_and_sitemaps(self):
        r = parse_robots(
            "User-agent: *\nDisallow: /admin/\nAllow: /admin/public\n\n"
            "Sitemap: https://example.com/sitemap.xml\n"
        )
        assert r.sitemaps == ["https://example.com/sitemap.xml"]
        assert r.groups[0].user_agents == ["*"]
        assert r.groups[0].disallow == ["/admin/"] and r.groups[0].allow == ["/admin/public"]

    def test_multiple_sitemaps(self):
        r = parse_robots(
            "Sitemap: https://example.com/a.xml\nSitemap: https://example.com/b.xml\n"
        )
        assert r.sitemaps == ["https://example.com/a.xml", "https://example.com/b.xml"]

    def test_duplicate_sitemaps_removed(self):
        r = parse_robots("Sitemap: https://example.com/a.xml\nsitemap: https://example.com/a.xml\n")
        assert r.sitemaps == ["https://example.com/a.xml"]

    def test_invalid_and_empty_sitemap_values_ignored(self):
        r = parse_robots(
            "Sitemap:\nSitemap: not a url\nSitemap: ftp://example.com/a.xml\n"
            "Sitemap: /relative.xml\nSitemap: https://example.com/ok.xml\n"
        )
        assert r.sitemaps == ["https://example.com/ok.xml"]
        assert r.invalid_sitemaps == 4

    def test_no_sitemap_declaration(self):
        r = parse_robots("User-agent: *\nDisallow: /private\n")
        assert r.sitemaps == [] and not r.empty

    def test_empty_content(self):
        for content in (b"", b"   \n\n", ""):
            r = parse_robots(content)
            assert r.empty and r.sitemaps == []

    def test_malformed_content_never_raises(self):
        blob = b"\x00\xff\xfe garbage \x80\x81 :::: \n Sitemap\n<html><body>404</body></html>"
        r = parse_robots(blob)
        assert r.sitemaps == []

    def test_comments_case_bom_and_crlf(self):
        r = parse_robots(
            b"\xef\xbb\xbfSITEMAP: https://example.com/a.xml # main\r\nUser-Agent: bot\r\n"
        )
        assert r.sitemaps == ["https://example.com/a.xml"]
        assert r.groups[0].user_agents == ["bot"]

    def test_sitemap_outside_group_and_consecutive_agents(self):
        r = parse_robots(
            "Sitemap: https://example.com/a.xml\nUser-agent: a\nUser-agent: b\nDisallow: /x\n"
        )
        assert r.sitemaps == ["https://example.com/a.xml"]
        assert len(r.groups) == 1 and r.groups[0].user_agents == ["a", "b"]

    def test_declaration_cap(self):
        text = "".join(f"Sitemap: https://example.com/{i}.xml\n" for i in range(1500))
        assert len(parse_robots(text).sitemaps) == 1000
