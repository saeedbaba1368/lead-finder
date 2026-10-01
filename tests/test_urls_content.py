import pytest

from app.urls import RejectReason, UrlEvaluator, UrlPolicy, check_content_length, check_content_type, check_extension
from app.urls.content import parse_media_type, path_extension

P = UrlPolicy()


@pytest.mark.parametrize(
    "path,ext",
    [("/a/b.html", "html"), ("/a/B.PDF", "pdf"), ("/a.b/c", ""), ("/a/b", ""), ("/", ""), ("/file.tar.gz", "gz"),
     ("/x.verylongextension", ""), ("/a/b.php;jsessionid=1", "php"), ("/.htaccess", "htaccess"), ("/a.", "")],
)
def test_path_extension(path, ext):
    assert path_extension(path) == ext


class TestExtensions:
    @pytest.mark.parametrize("path", ["/", "/team", "/team/", "/about.html", "/about.htm", "/index.php", "/p.aspx", "/p.jsp", "/p.shtml", "/dotted.name/page", "/v1.2/team"])
    def test_page_like_paths_allowed(self, path):
        assert check_extension(path, P) is None

    @pytest.mark.parametrize(
        "path",
        ["/a.jpg", "/a.PNG", "/a.gif", "/a.svg", "/a.webp", "/a.mp4", "/a.mp3", "/a.zip", "/a.tar", "/a.exe", "/a.css",
         "/a.js", "/a.json", "/a.xml", "/a.rss", "/a.woff2", "/a.ttf", "/a.docx", "/a.xlsx", "/a.pptx", "/a.ics", "/a.vcf", "/a.csv", "/a.ico"],
    )
    def test_binary_and_asset_paths_rejected(self, path):
        assert check_extension(path, P) == path.rsplit(".", 1)[-1].lower()

    def test_pdf_and_txt_are_opt_in(self):
        assert check_extension("/cv.pdf", P) == "pdf"
        assert check_extension("/cv.pdf", UrlPolicy(allow_pdf=True)) is None
        assert check_extension("/robots.txt", P) == "txt"
        assert check_extension("/robots.txt", UrlPolicy(allow_plain_text=True)) is None


class TestContentType:
    @pytest.mark.parametrize("header", ["text/html", "TEXT/HTML", "text/html; charset=utf-8", " text/html ;charset=UTF-8", "application/xhtml+xml"])
    def test_html_eligible(self, header):
        assert check_content_type(header, P) is None

    @pytest.mark.parametrize(
        "header",
        ["image/png", "application/json", "application/pdf", "text/plain", "text/css", "application/javascript",
         "application/octet-stream", "video/mp4", "text/xml", "application/zip", "multipart/form-data", "garbage"],
    )
    def test_non_html_rejected(self, header):
        assert check_content_type(header, P) == RejectReason.CONTENT_TYPE_NOT_ELIGIBLE

    def test_opt_in_types(self):
        assert check_content_type("application/pdf", UrlPolicy(allow_pdf=True)) is None
        assert check_content_type("text/plain; charset=utf-8", UrlPolicy(allow_plain_text=True)) is None
        assert check_content_type("text/plain", UrlPolicy(allow_pdf=True)) is not None

    def test_missing_header_policy(self):
        assert check_content_type(None, P) is None
        assert check_content_type("", P) is None
        strict = UrlPolicy(allow_missing_content_type=False)
        assert check_content_type(None, strict) == RejectReason.CONTENT_TYPE_NOT_ELIGIBLE
        assert check_content_type("  ; charset=x", strict) == RejectReason.CONTENT_TYPE_NOT_ELIGIBLE

    def test_parse_media_type(self):
        assert parse_media_type("Text/HTML; Charset=UTF-8") == "text/html"
        assert parse_media_type(None) is None
        assert parse_media_type(";x") is None


class TestContentLength:
    def test_limits(self):
        p = UrlPolicy(max_content_length=1000)
        assert check_content_length(1000, p) is None
        assert check_content_length("1001", p) == RejectReason.CONTENT_TOO_LARGE
        assert check_content_length(" 500 ", p) is None

    def test_missing_or_garbage_passes(self):
        assert check_content_length(None, P) is None
        assert check_content_length("abc", P) is None


def test_evaluator_applies_extension_rule():
    ev = UrlEvaluator(seed_url="https://example.com")
    assert ev.evaluate("https://example.com/logo.png").reason == RejectReason.NON_HTML_EXTENSION
    assert ev.evaluate("https://example.com/team.html").allowed
    assert UrlEvaluator(UrlPolicy(allow_pdf=True), "https://example.com").evaluate("https://example.com/x.pdf").allowed
