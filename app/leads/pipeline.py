"""End-to-end lead creation (Phase 8.5): connect the crawler to lead aggregation and lead persistence.

    HTTP response -> HTML parsing -> contact/structured extraction -> page analysis      (BfsCrawler, unchanged)
                  -> business identity -> lead aggregation -> lead persistence            (LeadCollector, here)

`LeadCollector` is a `page_observer` for `BfsCrawler`. The crawler calls it once for every completed fetch, inside the
same SAVEPOINT that stores the page row. For a page that was fetched OK and parsed as HTML the collector

1. maps the already parsed page to a page-level `BusinessLead` (`BusinessLead.from_parsed_page`: identity, emails,
   phones, social profiles, address, page type, language; nothing is fetched or re-extracted), and
2. merges it into the stored lead of the site's registrable domain (`LeadRepository.merge`, which uses
   `aggregate_leads`), creating the lead on the first usable page.

Why per page and not once after the crawl: the lead and the page row are written in one SAVEPOINT, so they commit or roll
back together. A page that is stored as visited always has its data in the lead, which matters because a resumed crawl
never requests a visited page again. A crash or a database error therefore cannot leave a visited page whose data was
lost, and a rolled-back page is simply requested again on resume.

Properties:

- One lead per registrable domain (the 8.4 unique `leads.domain_id`): a resumed crawl, a restarted process, a second
  crawl of the same site and pages repeated by redirects all update the same row. A resumed run only sees the pages it
  fetches itself, and `merge` combines them with what the earlier runs stored (contacts are unioned, existing name,
  description and address are kept).
- Pages that are not OK, not HTML, empty or not parsed produce no lead and no error. A page that cannot be mapped (a
  `LeadDataError`, a bug) is logged as ``lead_build_failed`` and skipped; the crawl continues.
- A frozen (already completed) crawl, a cancelled run's unfetched pages and URLs left pending persist nothing here.
- No clock is read except the injectable `now` (used as `first_seen` / `last_seen` of the page-level lead).

Not here: scoring, verification, enrichment, CRM, outreach, or any change to what the crawler fetches or follows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.db.base import utcnow
from app.leads.metadata import WebsiteMetadata
from app.leads.model import BusinessLead

if TYPE_CHECKING:  # pragma: no cover
    from app.crawler.bfs import CrawledPage
    from app.repositories.lead import LeadRepository

logger = get_logger(__name__)


class LeadCollector:
    """`BfsCrawler(page_observer=LeadCollector(LeadRepository(session)))`: create/update the site's lead per page.

    `website` fixes the website of a *new* lead (default: the origin of the first page's URL). An already stored lead
    keeps its website (`LeadRepository.merge`). `now` supplies the time a page was seen.
    """

    def __init__(
        self, repository: LeadRepository, *, website: str | None = None, now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repository = repository
        self._website = website
        self._now = now

    def __call__(self, page: CrawledPage, crawl_id: int) -> None:
        lead = self.build(page)
        if lead is not None:
            # A database error propagates on purpose: the crawler rolls the page back with it (see module docstring).
            self._repository.merge(lead, crawl_id=crawl_id)
            self._merge_metadata(page, lead)

    def _merge_metadata(self, page: CrawledPage, lead: BusinessLead) -> None:
        """Phase 12.1: store the page's website metadata on the lead (stored values win; see `app.leads.metadata`)."""
        try:
            metadata = WebsiteMetadata.from_parsed_page(page.parsed, source_url=page.result.final_url or page.url)
        except Exception as exc:  # noqa: BLE001 - unusable metadata must not stop lead creation or the crawl
            logger.warning("metadata_build_failed", extra={"url": page.url, "error": repr(exc)})
            return
        if not metadata.is_empty:
            self._repository.merge_metadata(lead.identity.domain, metadata)

    def build(self, page: CrawledPage) -> BusinessLead | None:
        """The page-level lead of one crawled page, or None when the page has nothing to build a lead from."""
        if not page.ok or page.parsed is None:
            return None
        source_url = page.result.final_url or page.url  # the URL the parsed content really came from
        try:
            return BusinessLead.from_parsed_page(
                page.parsed, source_url=source_url, website=self._website, seen_at=self._now(),
            )
        except Exception as exc:  # noqa: BLE001 - one unusable page must not stop lead creation or the crawl
            logger.warning("lead_build_failed", extra={"url": page.url, "error": repr(exc)})
            return None
