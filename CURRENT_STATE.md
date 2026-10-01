# CURRENT_STATE
**Phase 12.1: website metadata enrichment — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v11.5`. pytest/sqlalchemy/httpx etc. were NOT installable in the authoring sandbox (no network), so the full suite and the database/migration tests (5 DB cases of `tests/test_website_metadata.py`, `tests/test_migrations.py`) were NOT RUN. Under a stand-in runner the 15 pure cases of the new test file passed, and the pure cases of the existing parser/lead test files gave the same pass/fail counts on `v11.5` and `v12.1`. Run `pip install -r requirements-dev.txt && pytest` before relying on this.

- Parser extension (`og_image`, `favicon_url`, `twitter_*`), `app.leads.WebsiteMetadata` / `merge_metadata` (`app/leads/metadata.py`), `leads.website_metadata` (migration `0006`), `LeadRepository.merge_metadata` / `load_metadata`, stored by `LeadCollector`. First stored value per field wins across pages. No email/phone/social/address enrichment, scoring, external APIs, UI or crawling change; exports unchanged. See `docs/leads.md`.
**Phase 11.5: Lead management interface integration — IMPLEMENTED; full pytest suite RUN (see CHANGELOG/report for counts).** Built on `v11.4`. `leadfinder leads list|search|export` now share one set of controls: filters (search/export), `--sort-by`, `--desc/--asc`, `--page`, `--page-size`, JSON/CSV export of exactly the selected page. All reuse the Phase 10 query; read-only, offline, UTF-8. No scoring, AI, verification, CRM, enrichment or outreach. Crawler unchanged. End-to-end test: `tests/test_lead_management_e2e.py`.
**Phase 11.4: Lead result pagination and output controls — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v11.3`. The full pytest suite was NOT RUN: pytest, sqlalchemy, typer, fastapi etc. are not installable in the authoring sandbox (no network). Under a minimal stand-in runner (pytest shim, third-party and DB modules stubbed) the 28 pure cases of `tests/test_lead_output_controls.py` passed; the 12 CLI/database cases of that file were NOT RUN. For regression, the pure cases of the existing lead test files gave exactly the same pass/fail counts on v11.3 and v11.4 under the same stand-in (the failures there are limitations of the stand-in, e.g. fixtures, not of the code); the real suite must be run with `pip install -r requirements-dev.txt && pytest` before relying on this.

- `leadfinder leads list|search` gain `--sort-by` (`business_name|domain|first_seen|last_seen`), `--desc/--asc`, `--page`, `--page-size`, reusing the Phase 10.3/10.4 query (`LeadQuery`). Defaults unchanged. Paged output: summary line on stderr; page beyond results = explanatory message, exit 0; invalid values = `invalid paging: ...`, exit 2, before the database is opened. Ties broken by domain (10.4). Read-only; no ranking/scoring. See `docs/leads.md`.
**Phase 11.3: Lead export interface — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v11.2`. The full pytest suite was NOT RUN (pytest/sqlalchemy/typer/fastapi etc. unavailable offline). Under a stand-in runner (pure modules only) the 20 non-CLI cases of `tests/test_lead_export_interface.py` passed. NOT RUN: the 14 CLI/database cases of that file (every `leads export` invocation), the CLI/database cases of 11.1/11.2 and 10.x, and every other existing test. Run `pytest` before relying on this.

- `leadfinder leads export` (`app/cli/main.py`) + `app/leads/export_command.py`: JSON/CSV of all or filtered stored leads via the Phase 10 exports; stdout or `--output`; `--excel-bom` for CSV; invalid format/filter exit 2 before any database access; empty selection is a valid empty export; read-only. No new formats. See `docs/leads.md`.
**Phase 11.2: Lead search interface — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v11.1`. The full pytest suite was NOT RUN (pytest/sqlalchemy/typer/fastapi etc. unavailable offline). Under a stand-in runner (pure modules only) the 10 non-CLI cases of `tests/test_lead_search.py` passed. NOT RUN: the 13 CLI/database cases of that file (every `leads search` invocation), the CLI/database cases of 11.1 and 10.x, and every other existing test. Run `pytest` before relying on this.

- `leadfinder leads search` (`app/cli/main.py`): `--domain/--language/--country/--city/--page-type` (repeatable), `--has-email/--no-email`, `--has-phone/--no-phone`, `--has-social-profile/--no-social-profile`; reuses the 10.3 `LeadFilter` through the Phase 10 query; invalid values exit 2 before the database is opened; read-only. No new filter logic. See `docs/leads.md`.
**Phase 11.1: Lead listing interface — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v10.5`. The full pytest suite was NOT RUN (pytest/sqlalchemy/typer/fastapi etc. unavailable offline). Under a stand-in runner (pure modules only) the 8 non-database cases of `tests/test_lead_listing.py` passed. NOT RUN: the 9 database/CLI cases of that file, the 11 earlier-phase database cases, and every existing database/CLI test. Run `pytest` before relying on this.

- `leadfinder leads list` (`app/cli/main.py`) + `app/leads/listing.py`: lists all stored leads (business_name, domain, website, email_count, phone_count, first_seen, last_seen) via the Phase 10 query; tab-separated text; `No leads found.` when empty; read-only. No new filtering, sorting, scoring, enrichment or crawling. See `docs/leads.md`.
**Phase 10.5: Lead query and export integration — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v10.4`. The full pytest suite was NOT RUN (pytest/sqlalchemy/alembic/fastapi/httpx/pydantic are unavailable offline). Under a stand-in runner (pure modules only): 34 new test cases passed; `test_lead_paging` 36 passed; `test_lead_filters` 32 passed. NOT RUN: the 6 database cases of `tests/test_lead_query_export.py`, the database cases of 10.1-10.4, and every other existing test. Run `pytest` before relying on this.

- `app.leads.LeadQuery` / `query_leads` / `query_stored_leads` / `query_to_json` / `query_to_csv` / `export_query_json` / `export_query_csv` / `write_query_json` / `write_query_csv` (`app/leads/query.py`): filter -> sort -> paginate -> export, query order kept, 10.1/10.2 formats unchanged, read-only. 10.1/10.2 functions gained keyword-only `preserve_order=False`. No scoring, AI, verification, enrichment or CRM. See `docs/leads.md`.
**Phase 10.4: Lead sorting and pagination — IMPLEMENTED; PARTIALLY VERIFIED.** Built on `v10.3`. The full pytest suite was NOT RUN (pytest/sqlalchemy/fastapi unavailable offline). The pure tests of `tests/test_lead_paging.py` (36 cases) passed under a stand-in runner; the one database test (`test_stored_leads_are_sorted_paged_and_not_modified`) and the 2077 existing tests were not run. Run `pytest` before relying on this.

- `app.leads.sort_leads` / `paginate` / `sort_and_paginate` / `sort_and_paginate_stored` / `Page` / `LeadPagingError` (`app/leads/paging.py`): sort by business_name, domain, first_seen, last_seen; ascending/descending; ties by domain then website; missing values last; 1-based pages, page size 1-1000; page beyond the end and empty results give empty pages; invalid values raise `LeadPagingError`. Read-only. No scoring or ranking. See `docs/leads.md`.
**Phase 10.3: Lead filtering — IMPLEMENTED; full pytest suite RUN: 2077 passed, 0 failed (2039 existing + 38 new).** Built on `v10.2`.

- `app.leads.LeadFilter` / `filter_leads` / `filter_stored_leads` / `LeadFilterError` (`app/leads/filters.py`): domain, language, page type, country, city, has_email, has_phone, has_social_profile; AND across criteria, OR within; composable with `&`; exact matching; sorted by domain; read-only. No scoring, fuzzy search, enrichment, CLI. See `docs/leads.md`.
**Phase 10.2: Lead CSV export — IMPLEMENTED; full pytest suite RUN: 2039 passed, 0 failed (2013 existing + 26 new).** Built on `v10.1`.

- `app.leads.leads_to_csv` / `export_stored_leads_csv` / `write_leads_csv` (`app/leads/export_csv.py`): fixed 18 columns, RFC 4180 escaping, UTF-8, sorted by domain, byte-stable, read-only. JSON export unchanged. No filtering or CLI. See `docs/leads.md`.
**Phase 10.1: Lead JSON export — IMPLEMENTED; full pytest suite RUN: 2013 passed, 0 failed (1994 existing + 19 new).** Built on `v9.5`.

- `app.leads.leads_to_json` / `lead_to_json` / `export_stored_leads` / `write_leads_json` (`app/leads/export.py`): `{version, count, leads[]}` of `BusinessLead.to_dict()`, UTF-8, sorted by domain, byte-stable, read-only. No CSV, filtering or CLI. See `docs/leads.md`.
**Phase 9.5: lead de-duplication integration — VERIFIED end to end; full pytest suite RUN: 1994 passed, 0 failed (1986 existing + 8 new).** Built on `v9.4`. No production code change; `tests/test_lead_dedup_integration.py` (8) exercises HTTP -> parse -> extract -> lead -> identity -> dedupe/merge -> persistence incl. resume, cancellation, lifecycle, repeated crawls. Limits in `docs/leads.md`.
**Phase 9.4: multi-page lead aggregation — IMPLEMENTED; full pytest suite RUN: 1986 passed, 0 failed (1970 existing + 16 new).** Built on `v9.3`.

- `app.leads.aggregate_pages` / `lead_conflicts` (`app/leads/multipage.py`): combined lead plus reported conflicts (name/description/address; kept value never overwritten) and source URLs per field/value. `LeadRepository.merge` logs `lead_conflict`. No model/migration change, no fuzzy matching, no enrichment. See `docs/leads.md`.
**Phase 9.3: exact Lead de-duplication — IMPLEMENTED; full pytest suite RUN: 1970 passed, 0 failed (1951 existing + 19 new).** Built on `v9.2`.

- `app.leads.merge_leads` / `deduplicate_leads` (`app/leads/dedupe.py`): exact identity (canonical domain) only; existing values preserved, missing fields filled, contacts unioned, seen range widens, deterministic order. `LeadRepository.merge` uses it (stored values win over a newer page's). No fuzzy matching, no AI, no migration. See `docs/leads.md`.
**Phase 9.2: lead identity — IMPLEMENTED; full pytest suite RUN: 1951 passed, 0 failed (1865 existing + 86 new).** See `PHASE_REPORT_9_2.md`. Built on `v9.1`.

- `app.leads.lead_identity(source)` / `BusinessLead.identity` -> `LeadIdentity(domain, key, digest)` (`app/leads/lead_identity.py`): deterministic identity from the canonical domain; `domain` equals the stored `domains.name`. Serializable with strict verification. No merging, de-duplication or fuzzy matching; persistence unchanged.
**Phase 9.1: canonical domain identity — IMPLEMENTED; full pytest suite RUN: 1865 passed, 0 failed (1740 existing + 125 new).** See `PHASE_REPORT_9_1.md`. Built on `v8.5`.

- `app.urls.domain_identity(value)` / `canonical_domain(value)` (`app/urls/domain_identity.py`): one deterministic identity per website. `website` = canonical URL (scheme/host normalised, default port and fragment removed, path kept, `www.` kept); `domain` = identity key (registrable domain, never `www.`, IPs/single labels/public suffixes as themselves). Used by `BusinessLead.domain`, `extract_identity`, `aggregate_leads` and the JSON-LD own-site check through `domain_of` / `same_site`.
- No de-duplication, lookup, repository, model, migration, setting or crawler change.
**Phase 8.5: end-to-end lead creation — IMPLEMENTED; verified only PARTIALLY (see `PHASE_REPORT_8_5.md`): pytest, sqlalchemy, alembic, httpx and pydantic are unavailable offline in the packaging environment, so the full pytest suite and the new database/loopback tests were NOT RUN.** Built on `v8.4` (whose database tests were also never run).

- `app.leads.LeadCollector` (`app/leads/pipeline.py`) is a `BfsCrawler(page_observer=...)` hook: per page fetched OK as HTML it builds the page-level lead and merges it (`LeadRepository.merge`) into the domain's lead, in the same SAVEPOINT as the page row. One lead per registrable domain; resumed, restarted and repeated crawls update the same lead; visited pages are never fetched again, and their data is already in the lead.
- Crawler behaviour is unchanged when no observer is given. No model/migration/setting/dependency change.
**Phase 8.4: lead persistence — IMPLEMENTED but NOT VERIFIED BY EXECUTION: the database tests and the full regression suite were NOT RUN (pytest/sqlalchemy/alembic/httpx unavailable offline in the packaging environment).** See `PHASE_REPORT_8_4.md`. Built on `v8.3`.

- `leads` table (migration `0005`), `app.models.Lead`, `app.repositories.LeadRepository` (`save`, `merge`, `load`, `load_all`, `get_by_domain`), `SaveResult`. One lead per domain, safe repeated saves, seen range never shrinks. Details in `docs/leads.md` and `docs/database.md`.
- Crawler, page/crawl persistence, resume and lifecycle unchanged; the crawler does not create leads.
**Phase 8.3: lead data aggregation — IMPLEMENTED. New tests (29) run with a local stand-in runner; the FULL pytest regression suite was NOT RUN (pytest/sqlalchemy/httpx unavailable offline in the packaging environment).** See `PHASE_REPORT_8_3.md`. Built on `v8.2` (last full run there: 1658 passed).

- `app.leads.aggregate_leads(leads, website=None)` / `aggregate_parsed_pages(pages, website=None)` / `PageSource`: merge the already extracted values of the pages of one site into one `BusinessLead` (union + de-duplication of emails, phones, social profiles; first non-empty name/description/address with the homepage first; language vote; primary page's type and source URL; seen range). Order-independent. Details in `docs/leads.md`.
- Crawler, parser, identity extraction and `BusinessLead` unchanged; no persistence of leads yet.
**Phase 8.2: business identity extraction — IMPLEMENTED; full pytest suite RUN (real pytest/sqlalchemy/alembic/httpx): 1658 passed, 0 failed.** See `PHASE_REPORT_8_2.md`. Built on `v8.1`.

- `app.leads.extract_identity(parsed, source_url=, website=None)` -> `BusinessIdentity` (business_name + name_source, website, domain, description + description_source, all name candidates, `conflict`). Order: JSON-LD (non-Person, own-domain) -> `og:site_name` -> `og:title` -> title brand part -> homepage `<h1>`. Details in `app/leads/identity.py` and `docs/leads.md`.
- `ParsedPage.metadata.og_site_name` added (parser reads `og:site_name`); `BusinessLead.from_parsed_page` now uses the identity rules. Crawling unchanged.
- Not implemented: scoring, enrichment, transliteration between Persian and English names, persistence/creation of leads by the crawler.
**Phase 8.1: business lead data model — IMPLEMENTED; full pytest suite RUN (real pytest/sqlalchemy/alembic/httpx): 1590 passed, 0 failed.** See `PHASE_REPORT_8_1.md`. Built on `v7.3.4`.

- `app.leads.BusinessLead` (frozen, validated, JSON-serialisable; `docs/leads.md`): business_name, website (required), domain (derived), description, emails, phones, address, social_profiles, page_type, language, source_url, first_seen, last_seen. Reuses `PhoneNumber`, `SocialLink`, `Address`, `PageCategory`; does not copy `ParsedPage` data.
- `BusinessLead.from_parsed_page(parsed, source_url=, website=, seen_at=)` maps already extracted values (page values + JSON-LD business values); the title is never used as a name; no clock is read.
- Not persisted and not created by the crawler: no table or migration in this phase. Not implemented: scoring, verification, enrichment, CRM, export, cross-website de-duplication.
**Phase 7.3.4: page analysis integration — IMPLEMENTED; full pytest suite RUN (real pytest/sqlalchemy/alembic/httpx): see `PHASE_REPORT_7_3_4.md` for the counts.** Built on `v7.3.3`.

- Pipeline per HTML page: HTTP response -> HTML parsing -> contact extraction -> structured data -> language -> classification -> metrics -> existing persistence/result handling. It already ran inside `parse_response` (called by `BfsCrawler._visit`); 7.3.4 verifies it end to end and removes a duplicate classification pass (`parse_html(html, url=None)` now receives the URL). Result: `CrawledPage.parsed`; only `pages.title` is persisted, as before.
- BFS, discovery, `max_pages`/`max_depth`/`max_crawl_time`/`request_delay`, persistence, resume, cancellation and lifecycle untouched. Tests: `tests/test_page_analysis_integration.py` (18).
**Phase 7.3.3: basic content quality metrics — IMPLEMENTED; new tests pass; full pytest regression suite NOT RUN in the packaging environment** (pytest/sqlalchemy/httpx/pydantic unavailable offline). See `PHASE_REPORT_7_3_3.md`. Built on `v7.3.2`.

- `ParsedPage.metrics` = `ContentMetrics`: `character_count` (len of visible text), `word_count` (whitespace tokens containing a letter/digit), `heading_count`, `link_count` (all `<a href>` with non-blank href, uncapped), `unique_link_count` (`len(links)`, capped by `MAX_LINKS_PER_PAGE`), `image_count` (`<img>` outside script/style/noscript/template), `email_count`, `phone_count`, `html_length` (characters of the decoded HTML), `text_html_ratio` (`character_count / html_length`, 4 places, 0.0 when empty). Empty page = all zeros.
- `html_length` and `text_html_ratio` are not part of `==`. Descriptive only; the crawler does not read the metrics.
**Phase 7.3.2: basic page classification — IMPLEMENTED; new tests pass; full pytest regression suite NOT RUN in the packaging environment** (pytest/sqlalchemy/httpx/pydantic unavailable offline). See `PHASE_REPORT_7_3_2.md`. Built on `v7.3.1`.

- `ParsedPage.classification` = `PageClassification(category, score, signals, ambiguous)`; categories: homepage, about, contact, services, product, blog, article, portfolio, careers, other. Weights: URL last segment 4 / earlier 3, title first part 2 / rest 1, h1 2, other headings 1, metadata 1, body phrases (cap 2), contact-details structure 2; minimum score 2; ties by fixed priority; `ambiguous` when the runner-up is within 1 point. Homepage = root URL (optional language prefix, `index.*`/`home`, no query string) and wins outright.
- Blog section as the last URL segment = `blog`; with a slug after it (or a `/YYYY/MM/slug` path) = `article`. English and Persian keywords are matched together (percent-encoded Persian URLs, ZWNJ, Arabic letter forms).
- Descriptive only; the crawler does not read it.
**Phase 7.3.1: content language and encoding detection — IMPLEMENTED; new tests pass; full pytest regression suite NOT RUN in the packaging environment** (pytest/sqlalchemy/httpx unavailable offline). See `PHASE_REPORT_7_3_1.md`. Built on `v7.2.4`.

- `ParsedPage.language` = `LanguageDetection(language, source, declared)`; `language` is `en`, `fa`, `mixed` (Persian + English), `unknown`, or another declared code (`de`); `source` is `html_lang`, `meta`, `content` or `none`. Declared metadata (`<html lang>`/`xml:lang`, then meta `content-language`, `language`, `dc.language`, `og:locale`) wins; content statistics are used only when nothing usable is declared. Declared metadata is not verified against the content.
- Content rules: Persian-script vs Latin letters in title + visible text (URLs/emails ignored). `mixed` needs >= 10 letters and >= 20% of the minority language; < 5 letters, or mostly other scripts, is `unknown`. Arabic-script text counts as Persian only with Persian-only letters or common Persian words.
- `ParsedPage.encoding`: codec name used by `parse_response` (None for `parse_html(str)`); decoding itself is unchanged.
**Phase 7.2.4: JSON-LD and structured business information — IMPLEMENTED; new tests pass; full pytest regression suite NOT RUN in the packaging environment** (pytest/sqlalchemy/httpx unavailable offline). See `PHASE_REPORT_7_2_4.md`. Built on `v7.2.3`.

- `parse_jsonld(blocks)` -> `StructuredBusiness(type, name, url, description, telephones, emails, addresses, same_as, social_links, logo)`; telephones reuse `PhoneNumber`, sameAs profiles reuse `SocialLink`; `contactPoint` telephone/email are included. Repeated entities are merged; output is sorted. Values stay inside the record: page-level `emails`/`phones`/`social_links` are unchanged (visible text / anchors only).
- `Address(street, city, region, postal_code, country, formatted)` from schema.org `address` (object, string, list) and `<address>` elements (microdata `itemprop` fields when present, otherwise only `formatted`). `ParsedPage.addresses` is unique and sorted.
- The parser now captures `<script type="application/ld+json">` text (visible text still excludes it); script/noscript/template/comment copies are ignored.
**Phase 7.2.3: social media link extraction — IMPLEMENTED; new tests pass; full pytest regression suite NOT RUN in the packaging environment** (pytest/sqlalchemy/httpx unavailable offline). See `PHASE_REPORT_7_2_3.md`. Built on `v7.2.2`.

- `app/crawler/social.py` (pure): `SocialPlatform` registry; `classify_social_url(href)` -> `SocialLink(platform, url)` canonical profile URL (https, canonical host, no query/fragment/trailing slash; case of the handle kept) or None. `ParsedPage.social_links` is unique (case-insensitive URL), sorted by platform then URL. Platform names: `facebook`, `instagram`, `linkedin`, `x` (twitter.com is canonicalised to x.com), `youtube`, `tiktok`.
- Only `<a href>` (absolute http(s) or `//`) is considered, also past the link cap. Share buttons, posts, videos, login pages and sub-pages are not profiles. Nothing is fetched; BFS scope is unchanged.
**Phase 7.2.2: phone extraction — IMPLEMENTED; new tests pass; full pytest regression suite NOT RUN in the packaging environment** (pytest/sqlalchemy/httpx unavailable offline). See `PHASE_REPORT_7_2_2.md`. Built on `v7.2.1`.

- `ParsedPage.phones`: `PhoneNumber(number, raw)`, de-duplicated by `number`, sorted by `number`. `number` = `+digits` for international (`+` or `00` prefix, `(0)` trunk dropped) or digits only for local (leading 0 kept); Persian/Arabic digits become ASCII, `raw` keeps the original. No country is assumed, so local and international forms of one line are not merged.
- Rejects dates, prices/currency, IDs/order numbers, ZIP/postal codes, IPs/versions, card/SSN/ISBN shapes, bare digit runs (unless labelled "Tel:" etc. or starting with a national 0). Script/style/comments are not scanned.
**Phase 7.2.1: email extraction — IMPLEMENTED; new tests pass; full regression suite NOT RUN in the packaging environment** (no pytest/sqlalchemy/httpx available offline). See `PHASE_REPORT_7_2_1.md`. Built on `v7.1`.

- `ParsedPage.emails` (tuple, lower-case, unique, sorted) from visible text and `mailto:` hrefs. Script/style/noscript/template content and HTML comments are ignored. Conservative validation rejects asset names (`logo@2x.png`), `a@b`, `..`, bad labels. Pure parsing, no network, not persisted.
- Tests: `tests/test_html_email_extraction.py`.

**Phase 7.1: HTML content parsing and basic page extraction — IMPLEMENTED and TESTED.** Checkpoint `v7.1.zip` (built on `v6.4`). See `PHASE_REPORT.md` for the executed test results. Not implemented: contact/email/phone extraction, SEO scoring, lead qualification, AI extraction, persistence of parsed text/headings/metadata.

## Phase 7.1
- `app/crawler/html_parser.py` (new, pure, stdlib `html.parser`): `parse_html`, `parse_response`, `decode_html`, `is_html_content_type`; `ParsedPage(title, text, headings, links, metadata)`, `Heading`, `PageMetadata`. `links` are raw `<a href>` values; URL normalisation/scoping remains with `extract_links` + `UrlGate`.
- `BfsCrawler._visit` parses each page fetched OK as HTML and sets `CrawledPage.parsed`; failures are logged (`html_parse_failed`) and leave `parsed=None`. `_store_page` writes the title to `pages.title` (no migration). Nothing else in the crawler, persistence, resume or lifecycle changed.
- Tests: `tests/test_html_parser.py`, `tests/test_crawler_html_integration.py`. Docs: end of `docs/crawler.md`.

**Phase 6.4 (previous checkpoint): crawl lifecycle, graceful cancellation and shutdown — IMPLEMENTED and TESTED.** Checkpoint `v6.4.zip` (built on `v6.3.3`). Full suite: 1025 passed, 0 failed, 0 skipped (real pytest/sqlalchemy/alembic/httpx). Not implemented: anything from later phases (no signal handlers/CLI cancel command, no background workers).

## Phase 6.4
- **Lifecycle** (`app/crawler/lifecycle.py`): `CrawlLifecycle` = created / running / stopping / completed / stopped / failed, exposed as `BfsCrawler.lifecycle` and in `CrawlStateReport.lifecycle` (+ `to_dict()`). It is *derived* from the existing `Crawl.status` + `stop_reason` (`lifecycle_of`); `stopping` is in-memory only. The repository transition table and `CANCELLED` stay terminal as before. A stopped crawl (cancelled, `max_pages`, `max_crawl_time`) keeps status `RUNNING` + a `stop_reason`, i.e. lifecycle STOPPED and resumable; `COMPLETED`/`FAILED` are terminal. `max_pages` therefore ends as STOPPED (resumable by raising the limit), not COMPLETED.
- **Cancellation:** `BfsCrawler.cancel()` (idempotent, sync, any task of the loop). No new request starts; a pending `request_delay` wait is interrupted; the request in flight is finished, processed, its links queued and the page stored; then `crawl()` returns `stop_reason=CANCELLED`, state persisted (`stop_reason=cancelled`, pages visited/pending kept). A cancel before `crawl()` applies to that call only. Cancelling the asyncio task itself records the same state without awaiting (the in-flight URL stays PENDING) and re-raises `CancelledError`.
- **Errors:** fatal exception -> crawl row `FAILED` (+ `error_message`), lifecycle FAILED, exception re-raised (never reported as completion). Persistence failure -> `CrawlPersistenceError`, lifecycle FAILED in memory, crawl row left `RUNNING` (resumable; the store is unreliable so no "finished" claim is written). Rejected seed -> FAILED as before. A `FAILED`/`CANCELLED` crawl cannot be restarted (`CrawlTerminalError`, no requests, no writes); re-running a `COMPLETED` crawl stays a harmless no-op that only reads.
- **Shutdown:** the existing `async with crawler` closes the HTTP client on every exit path; the end-of-run bookkeeping is shared (`_conclude`); persistence steps are SAVEPOINT-guarded so no partial state or open nested transaction remains.
- **Migration `0004`:** adds `cancelled` to `ck_crawls_crawlstopreason` (downgrade clears such values first). `tests/test_migrations.py`: head `0003` -> `0004` (5 lines).
- **Tests:** `tests/test_crawler_lifecycle.py` (20), `tests/test_crawler_lifecycle_integration.py` (1, start -> cancel -> inspect -> new crawler resumes -> completed), plus the existing 6.3 suites unchanged.
- Unchanged semantics: `max_pages`, `max_depth`, `max_crawl_time` (per run), `request_delay`, URL normalisation, HTTP client.

**Phase 6.3.3 (partial): crawl state inspection IMPLEMENTED and TESTED.** Checkpoint `v6.3.3.zip` (built on `v6.3.2.2.2B`). Full suite: see the count recorded at packaging (all passed, 0 failed). **The Phase 6.3.3 brief was cut off after "3. Optional CLI inspection"**; "persistence cleanup" and "operational reporting" were NOT implemented because their requirements never arrived.

## Phase 6.3.3
- `app/crawler/state.py` (new): `inspect_crawl_state(session, crawl_id) -> CrawlStateReport | None` (None = unknown crawl). Pure reads over the existing `crawls`/`pages` rows (nothing duplicated or stored): status, stop reason, seed, created/started/finished/last-update/last-checkpoint times, elapsed seconds, current depth, and counts total/discovered, visited, successful, failed, pending, skipped. `to_dict()` gives a fixed-key JSON-ready dict. No writes, no network; same state gives the same report. `last_update_at` = latest of the crawl row and its pages.
- `PageRepository`: new read-only `count_by_status(crawl_id)` and `last_updated(crawl_id)`.
- CLI: `crawl state CRAWL_ID [--json]` (exit 1 if the crawl does not exist).
- Tests: `tests/test_crawl_state.py` (fresh, unknown, per-status counts, determinism and read-only, interrupted and completed real crawls, CLI text/JSON/not found).
**Phase 6.3.2.2.2 (A + B): IMPLEMENTED and TESTED** — resume-safe crawl state. Checkpoint `v6.3.2.2.2B.zip` (built on `v6.3.2.2.2A`). Full suite with real `pytest`/`sqlalchemy`/`alembic`/`httpx`: **998 passed, 0 failed** (the new `tests/test_crawler_resume.py` passed 5 extra consecutive runs). Earlier notes below that say "NOT RUN" for 6.3.2.2.1 are superseded: those tests and migrations 0002/0003 now run green.

## Phase 6.3.2.2.2B (resume)
- **Storage = the existing tables.** Page rows are the BFS state: `FETCHED`/`FAILED` = requested (never requested again); `PENDING` = queued frontier (with `depth`, `parent_url`; queue order = `id`); `SKIPPED` = discovered but kept out of the queue by `max_pages`. `Crawl` holds config, `current_depth`, `elapsed_seconds`, `stop_reason`, `last_checkpoint_at`. No new tables or migrations in 2B.
- `app/crawler/resume.py` (new): `load_resume_state` rebuilds visited set, queue and frontier from the rows; `SKIPPED` rows are promoted back to `PENDING` while `max_pages` leaves room; `CrawlPersistenceError`.
- `BfsCrawler`: new optional `crawl_repository=` and `commit_checkpoints=` (default False = caller owns the transaction). With a `page_repository` + `crawl_id`, each discovered URL is stored when queued; on start stored state is reloaded (visited URLs and stored redirect targets are marked done, the `UrlGate` memory and trap counters are replayed with the normal normalisation rules, `max_pages` counts earlier requests). Each page row and the crawl progress are written in one SAVEPOINT; `crawl()` body split into `crawl()` + `_run_levels()` without changing the loop logic. `CrawlResult.previously_visited` makes `pending` correct on resumed runs.
- **Crawl status:** a crawl stopped by `max_pages`/`max_crawl_time` (or killed) stays `RUNNING` with `stop_reason` set, so it can continue; exhausted frontier -> `COMPLETED`; rejected seed -> `FAILED`. Terminal crawls are not changed.
- **Semantics chosen:** `max_crawl_time` is a per-run budget (`elapsed_seconds` accumulates); `max_pages` is a total across runs (raise it to continue); failed fetches count as attempted and are not retried; changing `max_depth` upward on resume is not supported (links beyond the old depth were never stored).
- **Persistence errors:** a `SQLAlchemyError` rolls back that step, logs `crawl_persistence_failed` and raises `CrawlPersistenceError` (cause chained, partial `CrawlResult` on `.result`); the failed URL stays `PENDING`, so nothing is falsely recorded as visited. Other exceptions are not caught.
- `PageRepository.create/get_or_create` gained optional `status=` / `parent_url=`. Without a repository the crawler behaves as before; CLI and configuration untouched.
- **Limits:** persistence is synchronous inside the async loop; `title`/`content_hash` are still not populated; no CLI resume command (later task).

**Phase 6.3.2.2.1: IMPLEMENTED (page-metadata persistence) — NOT VERIFIED BY EXECUTION.** Checkpoint `v6.3.2.2.1.zip` (built on `v6.3.2.1`, via steps A–E). Not implemented: resume, Playwright, extraction, anything from 6.3.2.2.2 onward.

## Phase 6.3.2.2.1 status
- **Files changed:** `app/models/page.py`, `app/models/enums.py`, `app/models/__init__.py` (model + `PageOutcome`); `app/repositories/page.py`; `app/crawler/bfs.py`; new `alembic/versions/0002_page_fetch_metadata.py`; new `tests/test_crawler_persistence.py`; `tests/test_migrations.py` (head revision `0001` -> `0002` on 5 lines only); `docs/database.md` (pages row, migration list, PageRepository row); this file. No other file differs from `v6.3.2.1`.
- **Model:** `Page` gained `final_url`, `charset`, `response_size`, `duration_seconds`, `redirect_count` (default 0), `redirect_chain` (JSON), `outcome` (`PageOutcome`, values mirror the crawler's `FetchOutcome`), `dedupe_key`; CHECKs `response_size_non_negative`, `duration_non_negative`, `redirect_count_non_negative`, `ck_pages_pageoutcome`; index `ix_pages_dedupe_key`.
- **Repository:** `create(..., dedupe_key=)`; `mark_fetched` / `mark_failed` take the metadata as optional keywords (None = unchanged; `redirect_count` derived from `redirect_chain`; `mark_fetched` defaults `outcome` to `OK`).
- **Crawler hook:** `BfsCrawler(page_repository=...)` and `crawl(seed, crawl_id=...)`; each completed fetch is stored right after it is appended to `result.pages` (`get_or_create`, then `mark_fetched`/`mark_failed`; `dedupe_key` = `Page.hash_url` of the normalised final URL). No repository = unchanged behaviour; repository without `crawl_id` raises `ValueError`. Caller owns the transaction; a fetch cut off by `max_crawl_time` is not stored; database errors propagate.
- **Migration status:** `0002` (revises `0001`, now head) written and statically checked only; it has NOT been run (upgrade, downgrade, SQLite batch drop of named CHECKs, offline PostgreSQL SQL, no-drift test).
- **Test status:** `tests/test_crawler_persistence.py` (covers success, HTTP failure, network failure, final_url, charset, size, duration, redirects, outcome, duplicates, normalised-equivalent duplicates, same URL in different crawls, repository and guard) is **NOT RUN**. The rest of the suite is **NOT RUN** after these changes; the last recorded full run (969 passed) predates Phase 6.3.2.2.1.
- **Verification limitations:** the authoring sandbox had no `sqlalchemy`, `alembic`, `pytest` or `httpx`. Only `py_compile` and static cross-checks were done (model vs migration vs repository vs crawler field names, enum values, constraint/index names, exports, test helper imports, file diff against `v6.3.2.1`). Run `pip install -r requirements-dev.txt && pytest` before relying on this checkpoint. Points to watch: SQLite batch downgrade; the `iso-8859-1`, 500 and PNG (`UNSUPPORTED_CONTENT_TYPE`) test cases; raw-vs-normalised final URL assumptions in the redirect and `dedupe_key` assertions.
- **Known limits:** persistence is synchronous inside the async loop; `title` and `content_hash` are not populated; no dedupe-key lookup method.

**Phase 6.3.1: COMPLETE** (crawl controls: `max_crawl_time`, `request_delay`, async rate limiting) — checkpoint `v6.3.1.zip` (built on `v6.2.zip`). **(At that checkpoint persistence was not implemented; see 6.3.2.2.1 above.)**

## Phase 6.3.2.1 (crawl-control verification/hardening, no new features)
- `max_crawl_time` / `request_delay` from 6.3.1 re-verified against the 6.3.2.1 spec; deadline is re-checked after the rate-limit wait; docs now say monotonic clock. `tests/test_crawler_controls.py`: 34 tests; full suite 969 passed. Persistence/resume still NOT implemented (next: 6.3.2.2).

## Phase 6.3.1 additions (`app/crawler/ratelimit.py`, `bfs.py`, `app/core/config.py`)
- `max_crawl_time` (`APP_CRAWLER_MAX_CRAWL_TIME`, 0 = unlimited): checked before every request; the in-flight request is bounded by the remaining budget (`asyncio.timeout_at`) and discarded if it runs out. Stop reason `max_crawl_time`; `CrawlResult` gained `max_crawl_time`, `request_delay`, `elapsed`, `time_limited`.
- `request_delay` (`APP_CRAWLER_REQUEST_DELAY`, 0 = none): minimum seconds between request starts via `RequestRateLimiter` (asyncio lock + `asyncio.sleep`, no blocking sleep). If the next slot falls after the deadline the crawler stops without sleeping.
- Requests are still sequential; HTTP client, link discovery, URL normalisation and BFS order unchanged. Defaults keep 6.2 behaviour.
- Tests: `tests/test_crawler_controls.py` (30, loopback servers). Docs: `docs/crawler.md`, `docs/configuration.md`, `.env.example`.
- Verified by running the full suite with real `pytest`/`httpx` (see `PHASE_REPORT.md`).

## Phase 6.2 additions (`app/crawler/links.py`, `bfs.py`)
- `extract_links` and `BfsCrawler` / `build_crawler` on top of the unchanged 6.1 client; URLs pass through `UrlGate` (normalise, scope, SSRF, dedupe, traps); external domains are never followed.
- Level-by-level BFS, sequential requests; `CrawlResult` tracks queued / visited / fetched / failed / pending / current depth / stop reason. Failed pages never stop the crawl; redirect targets are not fetched twice.
- `APP_CRAWLER_MAX_PAGES` (100), `APP_CRAWLER_MAX_DEPTH` (3); clean stops (`completed` / `max_pages` / `seed_rejected`).
- Tests: `tests/test_crawler_links.py`, `tests/test_crawler_bfs.py`. Docs: end of `docs/crawler.md`.
- **Verification caveat:** as in 6.1, the authoring sandbox had no `pytest`/`httpx`/network; see `PHASE_REPORT.md` for exactly what was run.

## Phase 6.1 additions (`app/crawler/`)
- `AsyncHttpClient`: shared `httpx.AsyncClient` (connection reuse), `fetch(url) -> PageResult`, never raises for per-URL failures. HTTP is the default fetch method.
- Configurable connect/read/write/pool timeouts, follow-redirects/max-redirects (manual following: hops recorded, loops detected), max response size, supported content types (`APP_CRAWLER_*`, 12 settings, documented).
- `PageResult`: url, final_url, outcome, status_code, content_type, charset, headers, size, body, redirects, duration, error.
- Optional `UrlEvaluator` + resolver to apply the URL policy / SSRF checks to the URL and every redirect hop.
- Docs: `docs/crawler.md`. Tests: `tests/test_crawler_http_client.py`.
- **Verification caveat:** see `PHASE_REPORT.md` — the authoring sandbox had no network, `pytest` or `httpx`, so the suite was NOT executed there. Run `pip install -r requirements-dev.txt && pytest` before relying on this checkpoint.

## Earlier state (Phase 5)

## Working
- Phases 1-3 unchanged in behaviour: FastAPI app, `/health`, settings, logging, CLI (`version`, `config`, `serve`, `db ...`), ten SQLAlchemy models, Alembic, unit of work, repositories.
- **URL engine (`app/urls/`, pure logic, no I/O):**
  - Normalisation: scheme/host case, IDNA, legacy IPv4 forms, IPv6, default ports, percent-encoding, dot segments, duplicate slashes, trailing-slash policy, fragment removal, tracking/session parameter removal, query sorting. Idempotent.
  - Registered-domain extraction (built-in public-suffix subset + configurable extras), scope, blocklist, `exact | www | relevant | all` subdomain modes.
  - SSRF: internal names, private/loopback/link-local/CGNAT/reserved/multicast ranges, IPv4-in-IPv6, cloud-metadata addresses (never unblockable), wildcard-DNS services; resolved-address validation with an injected resolver; redirect targets and canonical hints go through the same checks.
  - Content eligibility by extension, `Content-Type` and `Content-Length`.
  - Trap detection: depth, repeating path cycles, query-param count, pagination limits, offsets, calendar/archive year window, per-path query-variant cap, per-pattern calendar cap, empty-page cutoff.
  - Canonical URL extraction/validation and duplicate detection (`dedupe_key`, `SeenUrls`).
  - `UrlEvaluator` (stateless) and `UrlGate` (per-crawl admission control) compose everything.
- 18 new `APP_URL_*` settings (documented in `.env.example` and `docs/configuration.md`; tests enforce the docs stay in sync). `APP_URL_SSRF_ALLOW_PRIVATE_NETWORKS` is refused in production.
- 703 offline pytest tests pass. A test fixture now fails any test that attempts DNS or a socket connect.

## Phase 5.1 additions (`app/discovery/`)
- robots.txt discovery, `Sitemap:` extraction, `/sitemap.xml` fallback, basic `<urlset>` parsing, URL-policy integration via `UrlGate`, malformed/empty/unsafe input handling, tests added (`tests/test_discovery_*.py`, fake fetcher + loopback server).
- (5.1 scope note) Deferred to 5.2+: sitemap indexes, nested indexes, recursion, cycle detection, advanced resource limits, gzip sitemaps.
- Note: the URL engine is now called by the discovery pipeline; `UrlEvaluator` gained `check_extension=` (default unchanged).

## Phase 5.2 additions
- Sitemap index support (type from root element), multiple declarations, one-level child sitemap processing, duplicate-sitemap suppression, per-sitemap error isolation, URL policy on sitemap and page URLs, limits (`APP_DISCOVERY_MAX_SITEMAPS`, `APP_DISCOVERY_MAX_URLS`, `APP_DISCOVERY_MAX_BYTES`), tests (fake fetcher + loopback server).
- (5.2 followed indexes one level only; superseded by 5.3.)

## Phase 5.3 additions
- Recursive traversal of nested sitemap indexes, visited-set cycle detection (canonical `dedupe_key`), max depth (`APP_DISCOVERY_MAX_DEPTH`, default 5), sitemap-count limit (default 1000) and URL-count limit (default 100,000), malformed-branch isolation, unknown-root diagnostics, deterministic tests (fake fetcher + loopback server).

## Phase 5.4 additions (final hardening)
- XML: sitemaps are parsed with expat directly (streaming); DOCTYPE, entity declarations and external entity references are refused inside the parser (encoding-independent), parameter entities disabled; the older byte pre-check remains as defence in depth. No tree, no recursion.
- Network efficiency: once `APP_DISCOVERY_MAX_URLS` is spent no further sitemap is fetched (previously later top-level sources were still fetched). Verified by request counting: robots.txt once, every sitemap once, rejected/duplicate/cyclic references never requested.
- HTTP 429 now reported as `FetchStatus.RATE_LIMITED`; `SitemapOutcome.truncated` reports files over the 50,000-entry cap.
- New comprehensive loopback test (`tests/test_discovery_phase5_e2e.py`, 15 tests): robots, fallback, nested indexes, cycles, malformed/unknown roots, 403/404/429/500, oversize, connection failure, depth/sitemap/URL limits, XXE canary, policy bypass attempts.
- Docs finalised (supported / not supported, configuration table with defaults and safety purpose).
- 860 offline pytest tests pass (845 at 5.3 + 15).

## Phase 5 summary (complete)
robots.txt discovery and parsing, `Sitemap:` declarations, `/sitemap.xml` fallback, `<urlset>` and `<sitemapindex>` parsing, multiple and nested indexes, recursive traversal, visited-set cycle detection, URL normalisation/dedup/policy on every URL, malformed-sitemap isolation, resource limits (depth, sitemaps, URLs, bytes, redirects, timeout), deterministic tests. Sitemap/URL discovery only: it does not crawl pages.

## Not implemented (by design; crawler orchestration still absent)
Crawler (fetching, robots.txt, rate limiting, redirect following, DNS resolution in the request path), extraction pipeline, email finder, verification logic, frontend, background jobs, HTTP endpoints for the data, CLI commands for URL checking.

## Notes
- The app does not auto-migrate at startup; run `python -m app.cli db upgrade`.
- Only SQLite has been executed; PostgreSQL is covered by offline SQL generation only.
- The URL engine is not yet called by any application code; it is a library for the crawler phase.
- Public-suffix data is a subset (see `docs/urls.md`, "Known limitations").

## Not supported in Phase 5
Time budgets, per-host politeness, gzip sitemaps, Allow/Disallow evaluation, DNS pinning (see `docs/discovery.md`).

## Next
Await the next Phase 6 sub-phase. 6.1 (HTTP fetching) and 6.2 (link discovery, BFS, page/depth limits) exist.
