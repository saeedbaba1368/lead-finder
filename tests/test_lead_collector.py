"""Phase 8.5: `LeadCollector` unit tests. No database, no network: crawled pages are built by hand and the repository
is a recording stand-in. The end-to-end behaviour (crawl -> lead rows -> restart) is in `test_lead_pipeline_e2e.py`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import OperationalError

from app.crawler import CrawledPage, FetchError, FetchOutcome, PageResult, parse_html
from app.leads import BusinessLead, LeadCollector

SEEN = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
HOME_HTML = (
    "<html lang='en'><head><title>Acme Plumbing</title><meta name='description' content='Family plumbers.'></head>"
    "<body><h1>Acme Plumbing</h1><p>Email info@acme.example</p></body></html>"
)


def result(url: str, *, final_url: str | None = None, outcome: FetchOutcome = FetchOutcome.OK) -> PageResult:
    error = None if outcome is FetchOutcome.OK else FetchError(outcome, outcome.value)
    return PageResult(url, final_url or url, outcome, 200 if error is None else 500, "text/html", "utf-8",
                      {}, 10, b"x", error=error)


def crawled(url: str = "https://acme.example/", *, html: str | None = HOME_HTML, **kwargs) -> CrawledPage:
    res = result(url, **kwargs)
    parsed = parse_html(html, res.final_url) if html is not None else None
    return CrawledPage(url, 0, None, res, parsed=parsed)


class RecordingRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[BusinessLead, int | None]] = []
        self._error = error

    def merge(self, lead: BusinessLead, *, crawl_id: int | None = None):
        if self._error is not None:
            raise self._error
        self.calls.append((lead, crawl_id))

    def merge_metadata(self, domain: str, metadata) -> bool:  # Phase 12.1: the collector also stores website metadata
        return False


def collector(repo: RecordingRepository, **kwargs) -> LeadCollector:
    return LeadCollector(repo, now=lambda: SEEN, **kwargs)  # type: ignore[arg-type]


def test_a_parsed_page_becomes_a_page_level_lead_merged_with_the_crawl_id():
    repo = RecordingRepository()
    collector(repo)(crawled(), 7)
    assert len(repo.calls) == 1
    lead, crawl_id = repo.calls[0]
    assert crawl_id == 7
    assert lead.business_name == "Acme Plumbing" and lead.description == "Family plumbers."
    assert lead.emails == ("info@acme.example",)
    assert lead.domain == "acme.example" and lead.website == "https://acme.example/"
    assert lead.page_type.value == "homepage" and lead.language == "en"
    assert lead.first_seen == lead.last_seen == SEEN


def test_the_lead_is_built_from_the_final_url_of_a_redirected_page():
    repo = RecordingRepository()
    collector(repo)(crawled("https://acme.example/old", final_url="https://acme.example/"), 1)
    assert repo.calls[0][0].source_url == "https://acme.example/"


def test_an_explicit_website_is_used_for_the_lead():
    repo = RecordingRepository()
    collector(repo, website="https://www.acme.example/")(crawled(), 1)
    assert repo.calls[0][0].website == "https://www.acme.example/"


@pytest.mark.parametrize("outcome", [FetchOutcome.HTTP_ERROR, FetchOutcome.TIMEOUT, FetchOutcome.UNSUPPORTED_CONTENT_TYPE])
def test_pages_that_were_not_fetched_ok_create_no_lead(outcome):
    repo = RecordingRepository()
    collector(repo)(crawled(outcome=outcome), 1)
    assert repo.calls == []


def test_a_page_without_parsed_content_creates_no_lead():
    repo = RecordingRepository()
    collector(repo)(crawled(html=None), 1)
    assert repo.calls == [] and collector(repo).build(crawled(html=None)) is None


def test_a_page_that_cannot_be_mapped_is_skipped_not_raised(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("unmappable")

    monkeypatch.setattr(BusinessLead, "from_parsed_page", boom)
    repo = RecordingRepository()
    collector(repo)(crawled(), 1)  # must not raise
    assert repo.calls == []


def test_a_database_error_is_not_swallowed():
    """The crawler rolls the page back together with the failed lead write; swallowing it here would lose data."""
    repo = RecordingRepository(error=OperationalError("INSERT leads", {}, Exception("disk I/O error")))
    with pytest.raises(OperationalError):
        collector(repo)(crawled(), 1)


def test_build_is_deterministic():
    one, two = collector(RecordingRepository()), collector(RecordingRepository())
    assert one.build(crawled()) == two.build(crawled())
