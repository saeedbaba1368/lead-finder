# Changelog
## [Unreleased] - Phase 12.1 (website metadata enrichment)
### Added
- Parser: `PageMetadata` gained `og_image`, `favicon_url` (first `<link rel="icon" | "shortcut icon">`), `twitter_card/title/description/image` (raw values; `ParsedPage.to_dict()["metadata"]` has the new keys). No second parser.
- `app/leads/metadata.py`: `WebsiteMetadata` (title, description, keywords, favicon, canonical, Open Graph, Twitter; all optional; URLs resolved against the page and normalised with `normalize_url`, unusable ones become None) and `merge_metadata` (stored value wins per field; a page only fills missing ones).
- `leads.website_metadata` JSON column (migration `0006`), `LeadRepository.merge_metadata` / `load_metadata`, and `LeadCollector` stores each page's metadata after merging the lead. `BusinessLead`, its `to_dict()` and the JSON/CSV exports are unchanged.
- `tests/test_website_metadata.py`; the recording stand-in in `tests/test_lead_collector.py` gained a no-op `merge_metadata`; `tests/test_migrations.py` expects head `0006`.
## [Unreleased] - Phase 11.5 (lead management interface integration)
### Added
- `leads export` gained `--sort-by`, `--desc/--asc`, `--page`, `--page-size` (same options and validation as `leads list|search`). Flow: stored leads -> 10.3 filter -> 10.4 sort -> 10.4 paginate -> 10.1 JSON / 10.2 CSV. No option = unchanged output (all leads, domain order).
- `app/leads/export_command.py`: `select_lead_page(repository, flt, sort_by, descending, page, page_size)` -> `QueryResult`; `select_leads` unchanged.
- `tests/test_lead_management_e2e.py` (2 tests): load -> filter -> sort -> paginate -> JSON/CSV export -> stored data unchanged.
### Fixed
- Three CLI help tests (11.1, 11.2, 11.3) asserted that `--sort`/`--page` options do not exist; they were stale since 11.4 added them. They now check the options that are still out of scope.
## [Unreleased] - Phase 11.4 (lead result pagination and output controls)
### Added
- `leads list` and `leads search` options `--sort-by business_name|domain|first_seen|last_seen`, `--desc/--asc`, `--page`, `--page-size`. Reuse the Phase 10 query (10.3 filter -> 10.4 sort -> 10.4 paginate); no new sorting/paging logic, no ranking or scoring.
- `app/leads/listing.py`: `list_leads_page(repository, flt, sort_by, descending, page, page_size)` -> `LeadListing` (rows, total, page, page_size, total_pages, `empty_message`, `summary`).
- `tests/test_lead_output_controls.py`.
### Changed
- `leads list` gained the output options (no option = unchanged output: all leads, domain order, no paging). A paged result prints a one-line summary to standard error; a page beyond the results prints an explanatory message on standard output (exit 0). Invalid sort/page values print `invalid paging: ...` and exit 2 before the database is opened.
## [Unreleased] - Phase 11.3 (lead export interface)
### Added
- `leadfinder leads export` CLI command (JSON or CSV, optional filters, stdout or `--output`, `--excel-bom`) and `app/leads/export_command.py` (`render_export`, `write_export`, `select_leads`, `export_format`, `validate_export`, `LeadExportError`, `EXPORT_FORMATS`). Reuses the Phase 10 exports; read-only.
### Changed
- `leads search` builds its filter through a shared `_build_filter` helper (behaviour unchanged).
## [Unreleased] - Phase 11.2 (lead search interface)
### Added
- `leadfinder leads search` CLI command (domain, language, country, city, page type, has-email, has-phone, has-social-profile) using the 10.3 `LeadFilter`.
### Changed
- `list_stored_leads(repository, flt=None)` accepts an optional filter; `format_lead_listing(rows, empty=...)` accepts the empty-result message. Defaults keep `leads list` output unchanged.
## [Unreleased] - Phase 11.1 (lead listing interface)
### Added
- `leadfinder leads list` CLI command and `app/leads/listing.py` (`LeadRow`, `list_stored_leads`, `format_lead_listing`, exported from `app.leads`). Reuses the Phase 10 query; read-only.
## [Unreleased] - Phase 10.5 (lead query and export integration)
### Added
- `app/leads/query.py`: `LeadQuery`, `QueryResult`, `query_leads`, `query_stored_leads`, `query_to_json`, `query_to_csv`, `export_query_json`, `export_query_csv`, `write_query_json`, `write_query_csv` (exported from `app.leads`).
### Changed
- `leads_document`, `leads_to_json`, `leads_to_csv`, `write_leads_json`, `write_leads_csv` accept keyword-only `preserve_order` (default False: output unchanged).
## [Unreleased] - Phase 10.4 (lead sorting and pagination)
### Added
- `app/leads/paging.py`: `sort_leads`, `paginate`, `sort_and_paginate`, `sort_and_paginate_stored`, `Page`, `LeadPagingError` (exported from `app.leads`). Sort by business_name/domain/first_seen/last_seen, ascending/descending, deterministic tie-breaking, 1-based pages, clear errors. No scoring, ranking or stored-lead changes.
## [Unreleased] - Phase 10.3 (lead filtering)
### Added
- `app/leads/filters.py`: `LeadFilter`, `LeadFilterError`, `filter_leads`, `filter_stored_leads` (exported from `app.leads`).
## [Unreleased] - Phase 10.2 (lead CSV export)
### Added
- `app/leads/export_csv.py`: `leads_to_csv`, `export_stored_leads_csv`, `write_leads_csv` (exported from `app.leads`). JSON export untouched.
## [Unreleased] - Phase 10.1 (lead JSON export)
### Added
- `app/leads/export.py`: `leads_to_json`, `lead_to_json`, `export_stored_leads`, `write_leads_json` (exported from `app.leads`).
## [Unreleased] - Phase 9.5 (lead de-duplication integration)
### Added
- `tests/test_lead_dedup_integration.py`: end-to-end tests of the crawler -> lead -> identity -> de-duplication -> persistence path. No production code change.
## [Unreleased] - Phase 9.4 (multi-page lead aggregation)
### Added
- `app/leads/multipage.py`: `aggregate_pages`, `AggregatedLead`, `LeadConflict`, `lead_conflicts` (conflicts reported, never overwritten; sources per field/value).
### Changed
- `LeadRepository.merge` logs a `lead_conflict` warning when a new page disagrees with the stored lead.
## [Unreleased] - Phase 9.3 (exact lead de-duplication)
### Added
- `app/leads/dedupe.py`: `merge_leads`, `deduplicate_leads` (exported from `app.leads`). Exact identity only; existing values win, gaps are filled, contacts unioned.
### Changed
- `LeadRepository.merge` uses `merge_leads` (stored values are no longer overridden by a newer homepage lead).
## [Unreleased] - Phase 9.2 (lead identity)
### Added
- `app/leads/lead_identity.py`: `LeadIdentity(domain, key, digest)` and `lead_identity(source)` (exported from `app.leads`), plus `BusinessLead.identity`. Derived only from the canonical domain: `key` = `lead:v1:<domain>`, `digest` = SHA-256 of the key (UTF-8). Deterministic, restart-stable, independent of page order, path, scheme, port and `www.`, Unicode-safe, serializable (`to_dict`/`to_json`/`from_dict`/`from_json`, strict verification on read). Equal to the stored `domains.name`.
- `tests/test_lead_identity.py` (86 cases): same domain, different paths, http/https, www/non-www, different domains, repeated generation, page-order permutations, stability across processes and hash seeds, Unicode, serialization, SQLite persistence compatibility.
### Notes
- No merging, de-duplication, fuzzy matching, lookup, repository, model-column, migration, setting, dependency, crawler or URL-engine change.
## [Unreleased] - Phase 9.1 (canonical domain identity)
### Added
- `app/urls/domain_identity.py`: `domain_identity(value)` -> `DomainIdentity(domain, host, website, scheme, port, path)`, `canonical_domain(value)`, `identity_of_host(host)`, `WEBSITE_POLICY` (exported from `app.urls`: `DomainIdentity`, `domain_identity`, `canonical_domain`). Normalises scheme, host (lower-case, punycode, trailing dot), default ports and fragments, keeps meaningful paths, and defines the domain key (registrable domain, never `www.`, IP literals and public suffixes as themselves). Scheme-less values get a default scheme.
- `app.leads.identity.same_site(url, domain)`.
- `tests/test_domain_identity.py` (125 cases, no network, no database).
### Changed
- `domain_of` (all Lead domains) is derived from the canonical identity; it raises `LeadDataError` for an unusable URL instead of returning `""`.
- `www.<public suffix>` (`www.co.uk`, `www.github.io`) now has the identity of the suffix, like every other `www.` host.
- JSON-LD own-site check uses the canonical identity: Unicode/trailing-dot/scheme-less record URLs now match their site; a `mailto:`/garbage record URL no longer counts as this site.
- An explicit `BusinessLead(domain=...)` is accepted in any spelling of the same identity (`www.acme.example`, `https://acme.example/`).
- Docs: `docs/urls.md`, `docs/leads.md`, `README.md`, `CURRENT_STATE.md`.
### Notes
- No lead de-duplication, lookup, repository, model, migration, setting, dependency, crawler, URL-discovery or `normalize_url` change. `BusinessLead.website` still requires a scheme.
## [Unreleased] - Phase 8.5 (end-to-end lead creation)
### Added
- `app/leads/pipeline.py`: `LeadCollector(repository, website=None, now=utcnow)`, a crawler page observer (exported from `app.leads`). For every page fetched OK and parsed as HTML it builds the page-level `BusinessLead` (`BusinessLead.from_parsed_page`) and merges it into the stored lead of the domain (`LeadRepository.merge`, which uses `aggregate_leads`). Pipeline: HTTP response -> HTML parsing -> contact/structured extraction -> page analysis -> business identity -> lead aggregation -> lead persistence.
- `BfsCrawler(page_observer=...)` and `PageObserver` (exported from `app.crawler`): an optional `(CrawledPage, crawl_id)` callback run for every completed fetch inside the page's own SAVEPOINT, so the lead and the page row commit or roll back together. A database error from the observer rolls the page back too (the page is requested again on resume); any other observer error is logged (`page_observer_failed`) and rolled back for that page only.
- `tests/test_lead_collector.py` (8 test functions, 10 cases; no database) and `tests/test_lead_pipeline_e2e.py` (9 test functions, 10 cases; loopback server + SQLite file, restart/resume, cancellation, failed lead write).
### Changed
- Docs (`README.md`, `CURRENT_STATE.md`, `docs/leads.md`, `docs/crawler.md`): the crawler can now create leads (opt-in, through the observer).
### Notes
- Default behaviour is unchanged: without `page_observer` the crawler runs the exact same code path. No model, migration, setting, dependency, URL/robots/sitemap, parser, identity, aggregation or repository change. No scoring, AI, verification, enrichment, CRM or outreach.
## [Unreleased] - Phase 8.4 (lead persistence)
### Added
- `leads` table (model `app.models.Lead`, Alembic `0005_leads`, head is now `0005`): one lead per domain (unique `domain_id`), optional `crawl_id` (`ON DELETE SET NULL`), JSON contact columns.
- `app/repositories/lead.py`: `LeadRepository.save` (upsert by domain, idempotent, seen range never shrinks), `merge` (via `aggregate_leads`), `load`, `load_all`, `get_by_domain`; `SaveResult`. `Domain.leads` relationship (cascade).
- `tests/test_lead_persistence.py` (33 test functions).
### Changed
- `tests/test_migrations.py` (head `0004` -> `0005`), `tests/test_models.py` (`leads` in the expected tables).
### Notes
- No change to the crawler, page/crawl repositories, resume or lifecycle; no CRM, enrichment, setting or dependency. The crawler does not create leads.
## [Unreleased] - Phase 8.3 (lead data aggregation)
### Added
- `app/leads/aggregate.py`: `aggregate_leads(leads, website=None)`, `aggregate_parsed_pages(pages, website=None)` and `PageSource`; exported from `app.leads`. Combines identity, emails, phones, address, social profiles, description, page type, language and source URL of one or many pages of a site into one `BusinessLead`: duplicates removed, result independent of input order, leads of different domains refused.
- `tests/test_lead_aggregation.py` (29 test cases), aggregation section in `docs/leads.md`.
### Notes
- Pure data: no extraction mechanism, request, setting, migration, dependency or crawler change; `BusinessLead`, identity extraction and the parser are unchanged.
## [Unreleased] - Phase 8.2 (business identity extraction)
### Added
- `app/leads/identity.py`: `extract_identity(parsed, source_url=, website=None)` -> `BusinessIdentity(website, domain, business_name, name_source, candidates, conflict, description, description_source)`; `NameCandidate`. Name priority: JSON-LD business name, Open Graph (`og:site_name`, then `og:title`), page title (brand part), homepage `<h1>`. Conflicting candidates are reported (`conflict`), never merged or guessed. Description: JSON-LD, meta description, `og:description`.
- `app/leads/errors.py` (`LeadDataError` moved here; still importable from `app.leads` and `app.leads.model`).
- `PageMetadata.og_site_name` (+ `to_dict()["metadata"]["og_site_name"]`): the parser now also reads `<meta property="og:site_name">`.
- `tests/test_business_identity.py` (68 test cases), identity section in `docs/leads.md`.
### Changed
- `BusinessLead.from_parsed_page` takes website / name / description from `extract_identity` (before: first JSON-LD name only). A title-derived name is now possible; the 8.1 test that expected `None` for a non-matching inner-page title still passes with a title that names the page.
### Notes
- No change to crawling, URL discovery, persistence, resume, lifecycle; no new request, setting, migration or dependency. No scoring, enrichment or transliteration between scripts.
## [Unreleased] - Phase 8.1 (business lead data model)
### Added
- `app/leads/` (`model.py`): `BusinessLead` (frozen dataclass: business_name, website, domain, description, emails, phones, address, social_profiles, page_type, language, source_url, first_seen, last_seen) and `LeadDataError(ValueError)`. Reuses `PhoneNumber`, `SocialLink`, `Address` and `PageCategory`; only `website` is required.
- Deterministic normalisation (URL engine for website/source_url, derived registrable domain, lower-case/unique/sorted contacts, UTC timestamps), `to_dict` / `from_dict` / `to_json` / `from_json` (round-trip, strict validation), and `BusinessLead.from_parsed_page` (maps already extracted values; no new extraction).
- `tests/test_business_lead.py` (61 test cases), `docs/leads.md`.
### Notes
- Pure data: no table, migration, repository, setting, dependency or network. The crawler does not create leads yet. No scoring, verification, enrichment, CRM, export or cross-website de-duplication.
## [Unreleased] - Phase 7.3.4 (page analysis integration)
### Changed
- `parse_html(html, url=None)` / `_PageParser.result(url)`: the page URL is handed to the classifier directly, so `parse_response` no longer classifies a second time. Output is identical (the URL-based classification of 7.3.2); each analysis stage now runs once per page: contact extraction -> structured data -> language -> classification -> metrics.
### Added
- `tests/test_page_analysis_integration.py` (18 tests, loopback server + SQLite): every analysis result on one crawled page, per-page classification URL, one execution of each stage per page, crawl/links identical with and without analysis, `max_depth` / `max_pages` / `request_delay` / `max_crawl_time`, no duplicate requests, persistence, resume (incl. completed crawl), cancellation + resume, analysis failure isolation.
### Notes
- No new extraction, setting, migration or dependency. `bfs.py`, URL discovery, persistence, resume, cancellation and lifecycle are unchanged; the analysis result stays on `CrawledPage.parsed` and is not persisted (only the title, as before).
## [Unreleased] - Phase 7.3.3 (basic content quality metrics)
### Added
- `app/crawler/metrics.py`: `ContentMetrics(character_count, word_count, heading_count, link_count, unique_link_count, image_count, email_count, phone_count, html_length, text_html_ratio)` and `compute_metrics`.
- `ParsedPage.metrics` (also `to_dict()["metrics"]`). The parser now also counts `<img>` elements and all `<a href>` anchors (uncapped). `ContentMetrics` and `compute_metrics` exported from `app.crawler`.
- `tests/test_content_metrics.py` (41 test cases).
### Notes
- `html_length` / `text_html_ratio` are excluded from `==` so an empty document still equals `ParsedPage()`. Pure counting; crawler, links discovery, BFS, persistence and other extraction are unchanged; nothing is persisted.
## [Unreleased] - Phase 7.3.2 (basic page classification)
### Added
- `app/crawler/classify.py`: `PageCategory`, `PageClassification(category, score, signals, ambiguous)`, `classify_page(page, url=None)`. Deterministic heuristics over URL path (percent-decoded), title, headings, metadata and body text; English and Persian keywords.
- `ParsedPage.classification` (also `to_dict()["classification"]`); `parse_response` classifies with the response's final URL, `parse_html` falls back to the canonical / Open Graph URL. `PageCategory`, `PageClassification`, `classify_page` exported from `app.crawler`.
- `tests/test_page_classification.py` (93 test cases).
### Notes
- Descriptive only: nothing reads the classification, so crawling, link discovery and BFS order are unchanged and no page is excluded. No AI/LLM, no new dependency, nothing persisted.
## [Unreleased] - Phase 7.3.1 (content language and encoding detection)
### Added
- `app/crawler/language.py`: `LanguageDetection(language, source, declared)`, `detect_language`, `detect_from_content`, `normalize_language_tag`. Declared metadata first (`<html lang>` / `xml:lang`, then meta `content-language`, `language`, `dc.language`, `og:locale`), then script statistics of the visible text. Result: `en`, `fa`, `mixed` (Persian + English), `unknown`, or another declared primary code.
- `ParsedPage.language` and `ParsedPage.encoding` (both in `to_dict()`); `decode_html_with_encoding` (same decoding as `decode_html`, also returns the codec name; `parse_response` fills `encoding`). `LanguageDetection`, `detect_language`, `decode_html_with_encoding` exported from `app.crawler`.
- `tests/test_html_language.py` (58 test cases).
### Notes
- No external service, no new dependency. `decode_html` behaviour, HTTP client, BFS, URL normalisation, persistence, resume, lifecycle, rate limiting and other extraction are unchanged. Nothing is persisted.
## [Unreleased] - Phase 7.2.4 (JSON-LD and structured business information)
### Added
- `app/crawler/structured.py`: `parse_jsonld`, `Address`, `StructuredBusiness`; JSON-LD objects, arrays and `@graph` for Organization, LocalBusiness, ProfessionalService, Corporation, Person.
- `ParsedPage.addresses` (JSON-LD + `<address>` elements) and `ParsedPage.structured_data`; both in `to_dict()`. `Address` and `StructuredBusiness` exported from `app.crawler`.
- `tests/test_html_structured_data.py` (48 test cases).
### Notes
- Malformed JSON-LD is skipped (never raises). Email, phone and social extraction behaviour, HTTP client, BFS, URL normalisation, persistence, resume, lifecycle and rate limiting are unchanged. Nothing is persisted.
## [Unreleased] - Phase 7.2.3 (social media link extraction)
### Added
- `app/crawler/social.py`: data-driven platform registry (`SocialPlatform`, `SOCIAL_PLATFORMS`) for Facebook, Instagram, LinkedIn, X/Twitter, YouTube, TikTok; `classify_social_url`, `extract_social_links`, `SocialLink(platform, url)`.
- `ParsedPage.social_links` (also `to_dict()["social_links"]`); `SocialLink` and `extract_social_links` exported from `app.crawler`.
- `tests/test_html_social_extraction.py` (104 test cases).
### Notes
- Email/phone extraction, HTTP client, BFS, URL normalisation, persistence, resume, lifecycle and rate limiting are unchanged. Social links are detected only, never fetched, and not persisted.
## [Unreleased] - Phase 7.2.2 (phone extraction)
### Added
- `ParsedPage.phones` (tuple of `PhoneNumber(number, raw)`), also in `to_dict()["phones"]`; from visible text and `tel:` links. `PhoneNumber` exported from `app.crawler`.
- `tests/test_html_phone_extraction.py` (20 tests).
### Notes
- Email extraction behaviour, HTTP client, BFS, URL normalisation, persistence, resume, lifecycle and rate limiting are unchanged. Phones are not persisted.

## [Unreleased] - Phase 7.2.1 (email extraction)
### Added
- `ParsedPage.emails`: lower-case, de-duplicated, sorted addresses from visible text and `mailto:` links (also in `to_dict()["emails"]`).
- `extract_emails_from_text` helper in `app/crawler/html_parser.py`; `tests/test_html_email_extraction.py` (18 tests).
### Notes
- No change to HTTP client, BFS, URL normalisation, persistence, resume, lifecycle, rate limiting or other extraction. Emails are not persisted yet.


## [0.7.1] - 2026-10-01 — Phase 7.1: HTML content parsing and basic page extraction
### Added
- `app/crawler/html_parser.py` (stdlib `html.parser`, no new dependency): `parse_html(str) -> ParsedPage`, `parse_response(PageResult) -> ParsedPage | None`, `decode_html`, `is_html_content_type`; models `ParsedPage`, `Heading`, `PageMetadata`.
- Extracted: title, visible text (script/style/noscript/template excluded, whitespace normalised), `h1`-`h6` in order, raw `<a href>` values, meta description / keywords / robots, canonical URL, Open Graph title / description / URL.
- Encoding: BOM, HTTP charset, `<meta charset>`, UTF-8 fallback; Persian/mixed text, ZWNJ and entities preserved.
- `CrawledPage.parsed` (default `None`); `BfsCrawler` parses every page fetched OK as HTML. A parser failure is logged (`html_parse_failed`) and never stops or fails the crawl.
- Tests: `tests/test_html_parser.py` (94), `tests/test_crawler_html_integration.py` (10).
### Changed
- The page title is now stored in the existing `pages.title` column (no migration). Version 0.7.1. Docs: `docs/crawler.md`, `docs/architecture.md`, README, `CURRENT_STATE.md`, `PHASE_REPORT.md`.
- Unchanged: HTTP client, `extract_links` / `UrlGate` link discovery, BFS order, limits, rate limiting, persistence of visited state, resume, cancellation, lifecycle.
### Not included
- Contact/email/phone extraction, SEO scoring, lead qualification, AI extraction; parsed text/headings/metadata are not persisted.

## [Unreleased] — Phase 6.3.2.1: crawl-control verification and hardening
### Changed
- `BfsCrawler.crawl` re-checks the deadline after the rate-limit wait, so a slot that wakes at or after the deadline never enters the HTTP client (previously only cancelled by `timeout_at`).
- Docs: `max_crawl_time` is described as a monotonic-clock budget (not wall-clock); constructor vs settings zero/negative conventions and the "crawl deadline, not per-request timeout" distinction are stated.
### Added
- Tests (4, `tests/test_crawler_controls.py`, now 34): no `time.sleep`, monotonic clock independent of wall clock, late-waking slot starts no request, consistent state after a time-limited crawl.
### Not included
- Persistence, resume, Playwright, extraction (Phase 6.3.2.2+). No v6.3.zip yet.

## [0.6.3.1] - 2026-09-30 — Phase 6.3.1: Crawl controls and rate limiting
### Added
- `max_crawl_time` (`APP_CRAWLER_MAX_CRAWL_TIME`, default 0 = unlimited): maximum runtime (monotonic clock) checked before each request, also bounding the request in flight; `StopReason.MAX_CRAWL_TIME`.
- `request_delay` (`APP_CRAWLER_REQUEST_DELAY`, default 0): minimum delay between request starts, implemented by the new async `RequestRateLimiter` (`app/crawler/ratelimit.py`); no blocking sleeps, no new dependencies.
- `CrawlResult.max_crawl_time`, `request_delay`, `elapsed`, `time_limited`; `BfsCrawler(max_crawl_time=, request_delay=)`; `build_crawler` reads both settings.
- Tests: `tests/test_crawler_controls.py` (30).
### Changed
- Docs (`docs/crawler.md`, `docs/configuration.md`, architecture, README, `.env.example`), version 0.6.3.1. HTTP client, link discovery, URL normalisation and BFS order unchanged; defaults preserve 6.2 behaviour.
### Not included
- Persistence, resume, Playwright, extraction, concurrency.

## [0.6.2] - 2026-09-30 — Phase 6.2: Internal link discovery and BFS crawling
### Added
- `app/crawler/links.py`: `extract_links` (a/area hrefs, `<base href>`, strips, de-duplicates, skips mailto/tel/javascript/data/fragment/empty, tolerant of malformed HTML).
- `app/crawler/bfs.py`: `BfsCrawler`, `CrawlResult`, `CrawledPage`, `StopReason`, `build_crawler`. Level-by-level crawl reusing `AsyncHttpClient`/`PageResult` and the existing `UrlGate` (normalisation, scope, SSRF, extension, traps, duplicate suppression). Tracks queued / visited / fetched / failed / pending, current depth, rejections.
- `APP_CRAWLER_MAX_PAGES` (default 100) and `APP_CRAWLER_MAX_DEPTH` (default 3), documented in `.env.example` and `docs/configuration.md`.
- Tests: `tests/test_crawler_links.py`, `tests/test_crawler_bfs.py` (loopback site).
### Changed
- Docs (`docs/crawler.md`, architecture, README, PROJECT_RULES rule 2), version 0.6.2. The 6.1 HTTP client and models are unchanged.
### Not included
- Crawl-time limit, delay/rate limiting, persistence, Playwright, email/people extraction, concurrency, robots.txt evaluation.


## [0.6.1] - 2026-09-30 — Phase 6.1: Async HTTP client foundation
### Added
- `app/crawler/`: `AsyncHttpClient` (one shared `httpx.AsyncClient`, connection pooling), `HttpClientConfig`, `PageResult` / `FetchOutcome` / `FetchError` / `RedirectHop`.
- Configurable connect/read/write/pool timeouts, follow-redirects and max redirects (manual, per-hop validated, loop-detected), max response size (declared and streamed), supported content types, missing-content-type policy, user agent, pool limits: twelve `APP_CRAWLER_*` settings (documented in `.env.example` and `docs/configuration.md`).
- Contained failures: connection, timeout, invalid URL, redirect, HTTP status, unsupported content type, too large, policy/SSRF block, unexpected errors; `fetch` never raises for a URL failure.
- Optional `UrlEvaluator` + resolver integration (URL policy and DNS/SSRF check on the URL and every redirect hop).
- `docs/crawler.md`; `tests/test_crawler_http_client.py` (loopback server + `httpx.MockTransport`).
### Changed
- `httpx` moved from dev-only to runtime dependencies (`requirements.txt`, `pyproject.toml`). Version 0.6.1.
### Not included
- BFS, link discovery, max pages/depth/time, delay/rate limiting, visited-page persistence, Playwright, email/people extraction.

## [0.5.4] - 2026-09-30 — Phase 5 complete
### Added
- `tests/test_discovery_phase5_e2e.py`: comprehensive deterministic loopback test (robots.txt, root/products/blog/cycle/malformed/unknown sitemaps) that counts every request the server receives, plus limit, HTTP-error, connection-failure, XXE-canary and policy-bypass tests (15 tests).
- `FetchStatus.RATE_LIMITED` for HTTP 429 (no retries).
- `SitemapOutcome.truncated` when a file exceeds the 50,000-entry cap.
### Changed
- XML parsing now uses expat directly with handlers that refuse DOCTYPE, entity declarations and external entity references at parser level; parameter-entity parsing disabled; streaming, no recursion. `SitemapParseResult` API unchanged; parse-error `detail` for malformed XML is now `ExpatError` instead of `ParseError`.
- Once the page-URL budget (`APP_DISCOVERY_MAX_URLS`) is spent, no further sitemap is fetched, including later top-level sources.
- Docs finalised: supported / not supported, configuration table (defaults, meaning, safety purpose), corrected the claim that path case is ignored when de-duplicating sitemaps (it is significant; host case, scheme and fragment are not).
- Removed unused `noqa` markers; version 0.5.4.
### Not included
- Phase 6. Time budgets, per-host politeness, gzip sitemaps, Allow/Disallow evaluation and DNS pinning remain unsupported (documented).

## [0.5.3] - 2026-09-30
### Added
- Recursive sitemap discovery: an index may reference other indexes to any depth up to `max_depth`; depth-first, document order.
- Nested sitemap indexes (e.g. root-index -> products-index -> products-2026-index -> products-2026-01.xml -> pages).
- Cycle detection: visited set keyed by the project's `dedupe_key`; A -> B -> A and A -> B -> C -> A terminate, repeats counted in `duplicate_sitemap_refs`.
- Depth protection: `APP_DISCOVERY_MAX_DEPTH` (default 5, 0-20); deeper children are not fetched and are recorded (`depth_limit_reached`, `skipped_by_depth`, `SitemapOutcome.depth_limited`, log entry).
- Resource limits: `APP_DISCOVERY_MAX_SITEMAPS` now defaults to 1000 and `APP_DISCOVERY_MAX_URLS` to 100,000 (5.2 defaults were 50 / 50,000); traversal stops when either is hit.
- Diagnostics on `SitemapOutcome`: `depth`, `detail` (e.g. `root:rss` for unknown roots), `missing_locs`, `empty_locs`, `rejected_children`.
- Deterministic tests: `tests/test_discovery_recursive.py` (fake fetcher) plus nested/limit cases in `tests/test_discovery_tree_http.py` (loopback server).
### Changed
- Phase 5.2 "one level only" behaviour (`nested_index_skipped`) removed; its tests were replaced by recursive tests.
- Version bumped to 0.5.3.
### Not included (deferred)
- Time budgets, per-host politeness, gzip sitemaps and other advanced resource limits (Phase 5.4). Phase 5 is **not** complete.


## [0.5.2] - 2026-09-30
### Added
- Sitemap index support: `parse_sitemap` decides the type from the XML root (`SitemapKind.URLSET` / `INDEX`) and extracts `<loc>` from `<sitemap>` entries; `missing_locs` counts entries without `<loc>`.
- Multiple sitemap files: all robots.txt declarations are processed; an index's child sitemaps are fetched and their pages combined with mixed top-level sources, de-duplicated.
- Child sitemap processing is one level deep; a child that is itself an index is recorded (`nested_index_skipped`) and not followed.
- Duplicate sitemap references are fetched once (project `dedupe_key`).
- Error isolation: a failing child (404/403/5xx/timeout/oversize/blocked/malformed/empty/unknown root) is recorded per sitemap (`SitemapOutcome`, `DiscoveryResult.failed_sitemaps`) and does not stop siblings.
- URL policy enforced for declared sitemap URLs, child sitemap URLs and page URLs; child sitemap `<loc>` must be absolute.
- Limits: `APP_DISCOVERY_MAX_SITEMAPS` (50) and `APP_DISCOVERY_MAX_URLS` (50,000), alongside the existing `APP_DISCOVERY_MAX_BYTES`; `DiscoveryResult` reports `sitemap_limit_reached`, `skipped_sitemaps`, `url_limit_reached`.
- Tests: `test_discovery_index.py` (fake fetcher) and `test_discovery_tree_http.py` (loopback server with a realistic sitemap tree), plus parser and config tests.
### Changed
- A `<sitemapindex>` is no longer reported as `UNSUPPORTED` (Phase 5.1 behaviour); 5.1 tests that asserted that were updated. Unknown roots are still `UNSUPPORTED`.
- Version bumped to 0.5.2.
### Not included (deferred)
- Nested sitemap indexes, recursive traversal and cycle detection across a recursive walk (Phase 5.3). Phase 5 is **not** complete.


## [0.5.1] - 2026-09-30
### Added
- `app/discovery/`: Phase 5.1 sitemap discovery. robots.txt fetch and parse (`Sitemap`, `User-agent`, `Allow`, `Disallow`), `Sitemap:` extraction (empty/malformed/duplicate values ignored), `/sitemap.xml` fallback (only when no usable declaration exists), basic `<urlset>` `<loc>` parsing with namespace support, safe XML handling (DOCTYPE/ENTITY rejected, size cap, malformed/empty/unsupported statuses), page URLs admitted through the existing `UrlGate`, stdlib `HttpFetcher` (timeout, size cap, validated redirects, SSRF/DNS check, never raises).
- Settings `APP_DISCOVERY_TIMEOUT_SECONDS`, `APP_DISCOVERY_MAX_BYTES`, `APP_DISCOVERY_MAX_REDIRECTS`, `APP_DISCOVERY_USER_AGENT`.
- `docs/discovery.md`; tests `tests/test_discovery_*.py` (fake fetcher and loopback HTTP server only).
### Changed
- `UrlEvaluator.evaluate`, `evaluate_redirect`, `evaluate_normalized` accept keyword `check_extension=True` (default behaviour unchanged) so robots.txt/sitemap XML can be fetched without the page-extension rule.
- Version bumped to 0.5.1.
### Not included (deferred)
- Sitemap indexes, nested indexes, recursive traversal, cycle detection, advanced resource limits. Phase 5 is **not** complete.

## [0.4.0] - 2026-09-30
### Added
- `app/urls/`: URL engine and crawl policy — `normalize_url`, `UrlPolicy`, `UrlEvaluator`, `UrlGate`, `TrapTracker`, `SeenUrls`, canonical extraction/validation, content-type eligibility, SSRF guards, registered-domain and subdomain policy. No network access.
- 18 `APP_URL_*` settings (see `docs/configuration.md`); `APP_URL_SSRF_ALLOW_PRIVATE_NETWORKS` is rejected when `APP_ENV=production`.
- `docs/urls.md`.
- 622 tests across eight `tests/test_urls_*.py` files (normalisation, SSRF, domains, content, traps, canonical/dedupe, engine, policy/config/fuzz).
- Test-suite guard: DNS lookups and socket connects fail any test that attempts them.
### Fixed (found while testing, all within Phase 4 code)
- `urljoin` could raise `ValueError` on inputs such as `http://[`; now a clean `invalid_url` rejection.
- Canonical extraction kept reading `<link>` tags after `</head>` when both arrived in one chunk.
- Compact date values (`ym=202605`, `20260512`) were not recognised as calendar parameters.
- IPv4-translated IPv6 addresses (`::ffff:0:127.0.0.1`) were not unwrapped by the SSRF check.
- Host labels beginning or ending with `-` were accepted.
### Changed
- Version bumped to 0.4.0.

## [0.3.0] - 2026-09-30
### Added
- Alembic: `alembic.ini`, `alembic/env.py`, `script.py.mako`, migration `0001_initial_schema`, `app/db/migrate.py`.
- CLI: `db upgrade`, `db downgrade`, `db current`.
- Session management: `session_scope`, hardened `get_session`; `UnitOfWork` with savepoints and `get_uow` dependency.
- Repository layer (`app/repositories/`) for all ten models, with eager-loading variants and `NotFoundError` / `InvalidTransitionError`.
- `app/core/security.py` (API key generation and hashing).
- Tests: migrations (up/down/idempotent/no-drift/PostgreSQL SQL), repositories, unit of work, FastAPI dependency, SQLite savepoint regression.
- `alembic>=1.13` dependency; project rules 11-12.
### Fixed
- SQLite: savepoints issued before the first write committed the outer transaction (rollbacks did not undo work). Engine now applies the documented pysqlite BEGIN workaround.
- Version mismatch from Phase 2: the 0.2.0 changelog entry had not bumped `__version__`/`pyproject.toml` (so `/health` reported 0.1.0). Both are now 0.3.0.

## [0.2.0] - 2026-09-30
### Added
- `app/db/`: declarative base with naming convention, id/timestamp mixins, lazy engine and session helpers.
- `app/models/`: Domain, Crawl, Page, Email, EmailEvidence, Person, PersonEvidence, EmailVerification, EmailPattern, ApiKey, with enums, relationships, indexes, constraints, and cascade rules.
- Settings `APP_DATABASE_URL` and `APP_DB_ECHO`; `sqlalchemy>=2.0` dependency.
- `docs/database.md`; 25 new tests (constraints, cascades, SET NULL, indexes, PostgreSQL DDL compile).
### Changed
- `config` CLI command masks the password in `database_url`.

## [0.1.0] - 2026-09-30
### Added
- Python 3.12 project structure (`app/`, `tests/`, `docs/`, `alembic/`).
- FastAPI application factory and `/health` endpoint.
- Pydantic settings with `APP_` environment configuration and `.env.example`.
- Structured logging (JSON / plain).
- Typer CLI skeleton (`version`, `config`, `serve`).
- pytest suite (health/startup, config, logging, CLI).
- README, docs, PROJECT_RULES, CURRENT_STATE, PHASE_REPORT.
