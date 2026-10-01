# Business leads (Phase 8.1)

`app.leads.BusinessLead` is the internal representation of one business discovered by the crawler. It is a frozen dataclass, validated on construction (also by `dataclasses.replace`), with no network access, clock or database access.

| Field | Type | Notes |
|---|---|---|
| `website` | `str` (required) | http(s) URL, normalised with the URL engine (any port allowed) |
| `domain` | `str` | derived: the canonical domain identity of the website (`app.urls.domain_identity`: registrable domain, never `www.`, IDN hosts in punycode); an explicit value must have the same identity, in any spelling (`WWW.Acme.example`, `https://acme.example/`) |
| `business_name` | `str \| None` | whitespace collapsed; blank = `None` |
| `description` | `str \| None` | stripped; blank = `None` |
| `emails` | `tuple[str, ...]` | lower-case, unique, sorted; invalid values raise |
| `phones` | `tuple[PhoneNumber, ...]` | existing type; unique by `number`, sorted |
| `address` | `Address \| None` | existing type; an address without content becomes `None` |
| `social_profiles` | `tuple[SocialLink, ...]` | existing type; unique (case-insensitive URL), sorted |
| `page_type` | `PageCategory \| None` | existing enum |
| `language` | `str \| None` | code as in `ParsedPage.language` (`en`, `fa`, `mixed`, `unknown`, ...) |
| `source_url` | `str \| None` | normalised URL of the page the data came from |
| `first_seen`, `last_seen` | `datetime \| None` | timezone-aware, stored in UTC; `last_seen >= first_seen` |

Invalid input raises `LeadDataError` (a `ValueError`); optional values are never silently invented or dropped.

**Serialisation:** `to_dict()` (JSON-ready, fixed keys, `[]`/`None` for empty values, ISO 8601 timestamps), `to_json()` (sorted keys, non-ASCII kept, so Persian stays readable), `from_dict()` / `from_json()` (missing optional keys allowed; unknown keys, wrong types and invalid values rejected). `from_dict(lead.to_dict()) == lead`.

**From a page:** `BusinessLead.from_parsed_page(parsed, source_url=..., website=None, seen_at=None)` maps values the parser already extracted: website, name and description from `extract_identity` (below), emails/phones/social profiles from the page plus its JSON-LD businesses, the first JSON-LD address (else the first page address), the page classification and language. `website` defaults to the origin of `source_url`; `seen_at` sets both timestamps (None leaves them unset).

**Not included:** persistence (no table or migration yet), scoring, verification, enrichment, CRM, export, de-duplication across websites. The crawler does not create leads yet.

## Business identity extraction (Phase 8.2)

`extract_identity(parsed, source_url=..., website=None)` (`app/leads/identity.py`) reads an already parsed page and returns a `BusinessIdentity`. It makes no request and does not touch crawling.

**Business name**, first source that yields a candidate wins; every source's candidate is kept in `candidates`:

1. `json_ld`: `name` of a schema.org business record. `Person` records are skipped, and so are records whose `url` belongs to another registrable domain; records matching this site are preferred.
2. `og_site_name` (used as written), then `og_title` (reduced to its brand part).
3. `title`: reduced to its brand part.
4. `homepage_heading`: only on a homepage, the first `<h1>`, and only if it is short (at most 5 words / 60 characters) and does not end like a sentence.

**Brand part of a title** (`Contact us | Acme Plumbing`): split at `|  -  –  —  :  ·  •  »`; generic segments (English and Persian: Home, Contact us, About, خانه, تماس با ما, ...) are dropped; a segment matching the domain label wins; otherwise the first segment on a homepage and the last on other pages. A single-segment title of an inner page names the page, so it is accepted only when it matches the domain. If nothing qualifies the name is `None`; nothing is invented.

**Conflicts:** `conflict` is True when two candidates name different things. Names are compared after case-folding, removing punctuation/spaces/ZWNJ and unifying Arabic and Persian letter forms (ي/ی, ك/ک); one name containing the other counts as the same name. The priority order still decides `business_name`. English and Persian forms of a name are not transliterated, so they are reported as a conflict.

**Website / domain:** `website` defaults to the origin of `source_url` (or the given `website`), normalised by the URL engine; `domain` is its registrable domain (IDN hosts in punycode). An unusable URL raises `LeadDataError`.

**Description:** JSON-LD description, then meta description, then `og:description`; `description_source` says which.

The parser now also records `<meta property="og:site_name">` as `ParsedPage.metadata.og_site_name`.

## Lead data aggregation (Phase 8.3)

`aggregate_leads(leads, website=None)` and `aggregate_parsed_pages(pages, website=None)` (`app/leads/aggregate.py`) combine values that were **already extracted** into one `BusinessLead` per website. No request, no clock, no new extraction, no crawler change. `aggregate_parsed_pages` takes `PageSource(parsed, source_url, seen_at=None)` items or `(parsed, source_url)` tuples, maps each with `BusinessLead.from_parsed_page` (page values plus JSON-LD values: identity, emails, phones, address, social profiles, description, page type, language, source URL) and merges the results; one page is the simple case and gives the same lead as `from_parsed_page`.

**Order independence.** Leads are first put in a canonical order: a homepage lead first, then by `source_url`, then by their JSON text. The same pages in any order give the identical lead. The first lead is the *primary* lead.

| Field | Rule |
|---|---|
| `emails` | union, lower-case, unique, sorted |
| `phones` | union, unique by normalised `number`; of equal numbers the smallest `raw` text is kept; sorted by number |
| `social_profiles` | union, unique by case-insensitive URL (the smallest spelling is kept), sorted by platform then URL; the lead model's cap of 200 still applies |
| `business_name`, `description` | first non-empty value in canonical order (homepage wins; an inner page only fills a gap) |
| `address` | the first address in canonical order, taken whole (fields of different addresses are never mixed) |
| `language` | most common known language; `unknown` only if nothing else is known; ties go to the earlier page |
| `page_type`, `source_url` | those of the primary lead, so they describe the same page |
| `first_seen` / `last_seen` | earliest / latest value present |
| `website` | the explicit `website`, otherwise the primary lead's |

Leads of different registrable domains are never merged (`LeadDataError`), and so are no leads, non-`BusinessLead` values or an unusable `website`. Within one page, JSON-LD and HTML values are combined by `from_parsed_page` (JSON-LD name/description/address first, then HTML; contact values are unioned and de-duplicated).

Not included: scoring, verification, enrichment, persistence, cross-website de-duplication, choosing between conflicting names beyond the order above.

## Lead persistence (Phase 8.4)

`LeadRepository` (`app/repositories/lead.py`) stores `BusinessLead` values in the `leads` table (migration `0005`, model `app/models/lead.py`). Same technology as the rest of the project: SQLAlchemy + Alembic, repositories flush and never commit.

**Identity.** One lead per registrable domain. The lead row references the existing `domains` row (created with `DomainRepository.get_or_create`) through a unique `leads.domain_id`, so a second row for one site is impossible, also under a race (unique violation, then re-select and update). Different websites of one domain (`www.` or not) are the same lead.

**Table `leads`.** `domain_id` (unique, `ON DELETE CASCADE`), `crawl_id` (nullable, `ON DELETE SET NULL`: the crawl that last saved it), `website`, `business_name`, `description`, `emails` / `phones` / `address` / `social_profiles` (JSON, exactly as `BusinessLead.to_dict()`), `page_type`, `language`, `source_url`, `first_seen`, `last_seen`, `created_at`, `updated_at`. Indexes on `crawl_id` and `business_name`. Times are stored in UTC; a database that returns naive values (SQLite) is read back as UTC.

| Method | Behaviour |
|---|---|
| `save(lead, crawl_id=None)` | create, or update the domain's lead to exactly the given values. Returns `SaveResult(record, created, changed)`. Saving the same lead again changes nothing (`updated_at` untouched). `first_seen` never moves forward, `last_seen` never moves back; a missing time keeps the stored one. `crawl_id=None` keeps the stored link. |
| `merge(lead, crawl_id=None)` | like `save`, but first combined with the stored lead through `aggregate_leads` (contacts unioned, stored name/description/address kept). For adding what a resumed or later crawl found. |
| `load(domain)` / `load_all(limit, offset)` | `BusinessLead` (validated again by `from_dict`) or `None` / list ordered by row id. |
| `get_by_domain(domain)`, `get`, `list`, `count` | row access. |

**Crawl/page persistence is unchanged.** Nothing here writes crawl or page rows; leads are created by the crawler only through the opt-in Phase 8.5 `LeadCollector` (below), or by a caller that aggregates parsed pages and saves. Deleting a crawl keeps the lead (`crawl_id` becomes NULL); deleting the domain deletes its lead.

Not included: CRM, enrichment, scoring, export, deleting leads through the repository API beyond the inherited `delete`.

## End-to-end lead creation (Phase 8.5)

```python
from app.leads import LeadCollector
from app.repositories import CrawlRepository, LeadRepository, PageRepository

crawler = BfsCrawler(
    client, evaluator,
    page_repository=PageRepository(session), crawl_repository=CrawlRepository(session),
    commit_checkpoints=True,
    page_observer=LeadCollector(LeadRepository(session)),
)
await crawler.crawl(seed_url, crawl_id=crawl.id)
```

Pipeline: HTTP response -> HTML parsing -> contact/structured extraction -> page analysis (language, classification, metrics) -> business identity -> lead aggregation -> lead persistence. The first five stages are the existing crawler (`parse_response`); `LeadCollector` adds the last two.

**When.** The crawler calls the observer once for every completed fetch, *inside the SAVEPOINT that stores the page row*. The collector ignores pages that were not fetched OK, were not HTML, or were not parsed. For the others it builds the page-level lead (`BusinessLead.from_parsed_page`, from the final URL, `seen_at` = `now()`) and calls `LeadRepository.merge(lead, crawl_id=...)`, which creates the domain's lead or combines the page's values with the stored lead (`aggregate_leads`: contacts unioned, stored name/description/address kept, `first_seen` never forward, `last_seen` never back).

**Why per page.** A resumed crawl never requests a visited page again, so its data must already be in the lead when the page is recorded as visited. Writing both in one SAVEPOINT guarantees that: a database error while writing the lead rolls the page back too (the page is `pending` again and is fetched again on resume); with `commit_checkpoints=True` page and lead are committed together after each page. An error that is not a database error (a bug in the mapping) is logged (`lead_build_failed` / `page_observer_failed`) and skips that page's lead data only; the crawl continues.

**Guarantees** (tests in `tests/test_lead_pipeline_e2e.py`, `tests/test_lead_collector.py`):

- One lead per registrable domain across pages, restarts, resumes, repeated runs and new crawls of the same site.
- Resume after a restart: visited pages are not fetched again; the lead ends up equal (apart from the seen range) to the lead of an uninterrupted crawl.
- Cancellation: the in-flight page is finished and stored with its lead data; the crawl is `STOPPED` (resumable) and resumes to `COMPLETED`.
- A completed crawl run again fetches nothing and changes no lead.
- A site without a usable page (404, not HTML) creates no lead. A site with at least one parsed HTML page creates a lead even if it only knows the website.
- Without `page_observer` nothing changes in the crawler.

**Limits.** The `language` vote is per `aggregate_leads` call: when a lead is merged page by page, the stored lead counts as one vote against each new page, so on a site with mixed languages the language of an incrementally built lead can differ from the language of a one-shot aggregation (`page_type` / `source_url` follow the same stored-lead-first rule, homepage preferred). Lead data of a page is only in the lead if the observer was configured when that page was crawled: pages crawled earlier without it are not revisited. Only the data of the pages of a *single* site is merged (one domain per lead); `www.` and bare-domain pages are the same lead. No scoring, verification, enrichment, CRM or outreach.

## Canonical domain identity (Phase 9.1)

Every Lead domain comes from one function, `app.urls.domain_identity` (see `docs/urls.md`): `BusinessLead.domain`, `BusinessIdentity.domain`, `aggregate_leads` and the JSON-LD "is this record about this site" check all use it, so the stored `domains.name` of a lead is the same for every spelling of the site (scheme, case, `www.`, trailing dot, default port, fragment, path, punycode vs. Unicode).

* `domain_of(website)` raises `LeadDataError` for an unusable URL (it used to return `""`); `same_site(url, domain)` is the shared check.
* The JSON-LD check no longer has its own host logic: records whose `url` is Unicode (`https://münchen.de`), has a trailing dot or is scheme-less now match their site; a `url` that is not a usable website (`mailto:`, garbage) is treated as "another site" and skipped.
* An explicit `BusinessLead(domain=...)` is accepted when it has the same identity (`www.acme.example` for `https://acme.example`); a different identity still raises.
* `BusinessLead.website` still requires a scheme. Not here: de-duplication of leads, lookups by identity.

## Lead identity (Phase 9.2)

`lead_identity(source)` / `BusinessLead.identity` (`app/leads/lead_identity.py`) return a `LeadIdentity(domain, key, digest)`, derived only from the canonical domain of the website (Phase 9.1):

| Field | Value |
|---|---|
| `domain` | the canonical domain; exactly the stored `domains.name` of the lead (so `LeadRepository.get_by_domain(identity.domain)` finds it) |
| `key` | `lead:v1:<domain>` (namespaced, versioned) |
| `digest` | SHA-256 hex of `key` as UTF-8 (64 ASCII characters) |

* **Deterministic and restart-stable:** no `hash()`, ordering, time or randomness (tested across processes with different `PYTHONHASHSEED`). Golden digests are pinned in the tests.
* **Independent of page order and content:** scheme (`http`/`https`), port, `www.`, path, query, fragment, source page, name, emails etc. never take part; every page of a site and every ordering of an aggregation gives the same identity.
* **Unicode-safe:** IDN domains are punycode, the digest is taken over UTF-8 bytes; all fields are ASCII.
* **Serializable:** `to_dict` / `to_json` (compact, sorted keys) and `from_dict` / `from_json`, which re-derive `key` and `digest` and raise `LeadDataError` on any mismatch, unknown or missing field, wrong version or non-canonical domain. `LeadIdentity(domain)` takes only an already canonical domain; `lead_identity(...)` accepts a `BusinessLead`, `BusinessIdentity`, `DomainIdentity` or a string (URL, scheme-less URL, bare host).
* **Exact:** two leads share an identity only if their canonical domains are the same string. No fuzzy or name matching; hosting-platform tenants (`a.github.io`, `b.github.io`) stay separate.
* **Persistence:** no column, migration or repository change; the identity is the existing unique key.
* Not here: merging or de-duplicating leads, lookups by identity.

## Exact Lead de-duplication (Phase 9.3)

`app/leads/dedupe.py` (pure) and `LeadRepository.merge`. Two leads are duplicates only if their `LeadIdentity` (canonical domain) is the same string. No fuzzy, name or AI matching; `acme.example` and `acme-plumbing.example` stay separate.

* `merge_leads(existing, incoming)`: the existing lead wins, the incoming one only adds. `website`, `business_name`, `description`, `address` (whole, never mixed) are kept if set, else filled. `page_type` + `source_url` are one unit (existing pair kept when either is set). `language` is kept unless missing/`unknown`. `emails`, `phones`, `social_profiles` are unioned, existing spelling kept. Seen range only widens. Idempotent; different identities raise `LeadDataError`.
* `deduplicate_leads(leads)`: one lead per identity, folded in input order, result ordered by identity key.
* `LeadRepository.merge` now uses `merge_leads` with the stored lead (it used `aggregate_leads`, where a newer homepage lead could override stored values). One row per domain still comes from the unique `leads.domain_id`; no migration. `aggregate_leads` (one-shot merge of a site's pages) is unchanged.
* Tests: `tests/test_lead_dedupe.py` (19).

## Multi-page lead aggregation (Phase 9.4)

`aggregate_pages(leads, website=None)` (`app/leads/multipage.py`, pure) combines the page-level leads of one canonical website (homepage: name + email, contact page: phone + address, about page: description) into an `AggregatedLead(lead, conflicts, field_sources, value_sources)`.

* `lead` is exactly `aggregate_leads(...)`: order-independent, contacts unioned without duplicates, a gap is filled by the first page (homepage first, then `source_url`) that has a value, an existing value is never replaced.
* `conflicts` (`LeadConflict(field, kept, rejected, kept_source, rejected_source)`): a different non-empty `business_name`, `description` or `address` offered by another page. The kept value stays; the rejected one is reported once, never silently dropped. Comparison is exact (no fuzzy matching: `Acme Plumbing` vs `ACME plumbing` is a conflict). Multiple emails/phones/profiles are not conflicts.
* `field_sources` / `value_sources`: the page URL that supplied each kept scalar field, and all page URLs of every email / phone / social profile (`emails:<addr>`, `phones:<number>`, `social_profiles:<url>`). Uses `BusinessLead.source_url` only; nothing new is persisted.
* `lead_conflicts(leads)` checks priority-ordered leads; `LeadRepository.merge` uses it to log a `lead_conflict` warning when a new page disagrees with the stored lead (the stored value is kept).
* Different canonical domains raise `LeadDataError`. No enrichment, no AI. Tests: `tests/test_lead_multipage.py`.

## Lead de-duplication integration (Phase 9.5)

No production code changed in 9.5: the pieces were already connected, this phase verifies the whole path with deterministic end-to-end tests (`tests/test_lead_dedup_integration.py`, 8 tests; fixtures from `tests/test_lead_pipeline_e2e.py`).

    HTTP response -> parsing -> extraction -> page-level lead (LeadCollector.build) -> canonical identity (domain_identity / lead_identity)
      -> duplicate detection + merge (LeadRepository.merge = merge_leads) -> persistence (leads row, unique per domain)

Verified: every page-level lead of a crawl has the same `LeadIdentity`; the stored lead equals `aggregate_pages` of those page leads (content, apart from the seen range) and sources name the contributing pages; seeding at an inner page gives the same complete lead; two independent runs give identical leads; repeated crawls never add a row; a recrawl of a changed site adds new data, keeps stored data and logs `lead_conflict`; cancel -> resume -> new crawl ends with one correct lead and no page fetched twice, lifecycle STOPPED -> COMPLETED; `www.`/bare/`http`/explicit-port/redirect-to-same-site spellings are one lead, a lookalike domain is another, a subdomain belongs to its registrable domain; a redirect to another site stores the lead under the site that served the content.

Limits: the first sighting decides name, description, address, `website`, `page_type` and `source_url` (stored values win); a homepage found later does not replace a deeper page's `page_type`/`source_url`. Conflicts are only logged (and returned by `aggregate_pages`), not stored; a persistently disagreeing page logs once per merge. Concurrent writers on one domain rely on the unique `leads.domain_id`.

## Lead JSON export (Phase 10.1)

`app/leads/export.py` (read-only, offline). JSON only; no CSV, filtering or CLI command yet.

    {"version": 1, "count": <n>, "leads": [<BusinessLead.to_dict()>, ...]}

UTF-8 text, 2-space indent, one trailing newline, non-ASCII written as is (Persian text, ZWNJ and Persian digits survive). Every lead has the same fixed keys in `to_dict()` order, `null` for a missing optional value and `[]` for an empty collection; `BusinessLead.from_dict(entry)` reads an entry back. Leads are ordered by canonical domain, so the output depends on neither insertion order nor input order; the same leads always give the same bytes. An empty set exports `{"version": 1, "count": 0, "leads": []}`.

* `leads_to_json(leads)` (one or many), `lead_to_json(lead)`, `export_stored_leads(repository)` (all stored leads via `LeadRepository.load_all`, selects only: no flush, commit or `updated_at` change), `write_leads_json(path, leads)` (UTF-8, no BOM, temp file + atomic replace, nothing partial on failure).
* Two leads with the same identity raise `LeadDataError` instead of being dropped; de-duplicate first (`deduplicate_leads`).
* Not here: CSV, filtering, pagination options, CLI. Tests: `tests/test_lead_export.py` (19).

## Lead CSV export (Phase 10.2)

`app/leads/export_csv.py` (read-only, offline). CSV only; the JSON export (10.1) is unchanged; no filtering, no CLI.

* RFC 4180 style via the `csv` module: UTF-8, `\r\n` records, header always present (an empty export is the header line), fields quoted only when needed (comma, double quote, CR, LF), quotes doubled, line breaks inside a field kept. Persian text, ZWNJ and Persian digits are written as is.
* Fixed columns: `business_name, website, domain, description, emails, phones, address_street, address_city, address_region, address_postal_code, address_country, address_formatted, social_profiles, page_type, language, source_url, first_seen, last_seen`. Values come from `BusinessLead.to_dict()` (same source as the JSON export).
* `None`, empty collections and a missing address are empty cells. `emails`, `phones` (normalised numbers only; `raw` is not exported) and `social_profiles` (URLs) join several values with `|`; a value containing `|` raises `LeadDataError` instead of making the cell ambiguous.
* Rows are ordered by canonical domain, byte-stable, duplicate identities refused (as in JSON).
* `leads_to_csv(leads)`, `export_stored_leads_csv(repository)`, `write_leads_csv(path, leads, excel_bom=False)` (atomic; `excel_bom=True` adds a UTF-8 BOM so Excel detects Persian text).
* Limits: CSV is lossy compared with JSON (phone `raw`, social platform names); values are exported as stored, so a spreadsheet may interpret a text cell starting with `=`, `+`, `-` or `@` as a formula (crawled text is untrusted). Tests: `tests/test_lead_export_csv.py` (26).

## Lead filtering (Phase 10.3)

`app/leads/filters.py` (pure, offline). No scoring, fuzzy search, enrichment or CLI; stored leads are only read.

* `LeadFilter(domain, language, page_type, country, city, has_email, has_phone, has_social_profile)`: every criterion optional; all set criteria must hold (AND), several values of one criterion are alternatives (OR); no criteria = all leads. `has_*` take `True`/`False` (False = has none). A lead without the field (no language, page type or address) never matches a criterion on it.
* Matching is exact. `language`, `country`, `city` are compared after NFKC, case folding, whitespace collapsing and unifying Arabic ي/ى/ك with Persian ی/ک; ZWNJ is kept; `Teheran` does not match `Tehran`; `Tehran` does not match `تهران`. `domain` uses the canonical domain identity (any spelling, IDN or punycode); `page_type` takes a `PageCategory` or its name.
* Invalid input raises `LeadFilterError` (a `LeadDataError`) naming the criterion and value: unknown names (`LeadFilter.from_dict`, `filter_leads(..., **criteria)`), blank/non-text values, empty lists, non-boolean `has_*`, unknown page types (valid ones listed), unusable domains.
* `a & b` composes filters (commutative, associative, hashable); a criterion on both sides keeps the common values; no common value or `has_email=True & False` matches nothing (no error).
* `filter_leads(leads, flt=None, **criteria)` and `filter_stored_leads(repository, flt=None, **criteria)` return a list ordered by canonical domain, independent of input order; `leads_to_json(...)` / `leads_to_csv(...)` export a selection. Tests: `tests/test_lead_filters.py` (38).

## Lead sorting and pagination (Phase 10.4)

`app/leads/paging.py` (pure, offline). No scoring or ranking; stored leads are only read, never modified.

* `sort_leads(leads, sort_by="domain", descending=False)`: `sort_by` is `business_name` (NFKC, case-folded, whitespace-collapsed), `domain` (canonical domain), `first_seen` or `last_seen` (compared as instants, any UTC offset). Leads without a value (no/blank name, no moment) come last in both directions.
* Deterministic ties: equal values are ordered by canonical domain, then website text, always ascending (also when `descending=True`). The result never depends on input order.
* `paginate(items, page=1, page_size=50)` -> `Page(items, page, page_size, total)` with `total_pages`, `has_next`, `has_previous`. Pages are 1-based; a page beyond the last one and an empty result give an empty `items` (no error; `total_pages` is 0 when empty).
* Invalid input raises `LeadPagingError` (a `LeadDataError`) naming the argument and value: `page` / `page_size` not a whole number (floats, strings, `None`, `bool`), `page < 1`, `page_size < 1` or `> 1000`, unknown `sort_by` (valid fields listed), non-bool `descending`, non-lead items.
* `sort_and_paginate(leads, ...)` and `sort_and_paginate_stored(repository, ...)` validate first (a bad request never reads the database), then sort and slice. Compose with filtering: `sort_and_paginate(filter_leads(leads, ...), ...)`. Tests: `tests/test_lead_paging.py`.

## Lead query and export integration (Phase 10.5)

`app/leads/query.py` (pure, offline). Pipeline: stored leads -> filter (10.3) -> sort (10.4) -> paginate (10.4) -> JSON (10.1) / CSV (10.2). No scoring, AI, verification, enrichment or CRM; stored leads are only read.

* `LeadQuery(filter, sort_by="domain", descending=False, page=None, page_size=None)`: validated on creation (`LeadFilterError` / `LeadPagingError`), so a bad query fails before any read or write. `filter` is a `LeadFilter`, a criteria mapping or None. `page`/`page_size` both None = no paging (every match); giving either turns paging on (defaults page 1, size 50, max 1000). A query can also be passed as a mapping or as keyword arguments.
* `query_leads(leads, query)` / `query_stored_leads(repository, query)` -> `QueryResult(leads, total, page, page_size)` (`total_pages` None without paging). A page beyond the end or no match gives an empty selection, not an error.
* `query_to_json` / `query_to_csv` (in-memory) and `export_query_json` / `export_query_csv` / `write_query_json` / `write_query_csv` (stored leads) export exactly the selected leads in query order, in the unchanged 10.1/10.2 formats (JSON `{version, count, leads}`; `count` = selected leads). Paging metadata is not written into the files. JSON and CSV come from the same `to_dict()` values, so they hold the same leads, order and data. Persian text, ZWNJ, quotes, commas and line breaks are kept; files are UTF-8, written atomically, nothing is written when the query is invalid.
* 10.1/10.2 gain a keyword-only `preserve_order=False` (`leads_document`, `leads_to_json`, `leads_to_csv`, `write_leads_json`, `write_leads_csv`). The default still sorts by domain, so existing output is byte-identical; the query exports pass `True` to keep the sort order.
* Limits: the whole filtered set is loaded in memory before sorting (as in 10.3); two selected leads with one identity raise `LeadDataError`; CSV cells starting with `=`, `+`, `-`, `@` are not neutralised (see 10.2). Tests: `tests/test_lead_query_export.py`.

## Lead listing (Phase 11.1)

`leadfinder leads list` (CLI; `app/cli/main.py`) with the rows and text in `app/leads/listing.py`. Read-only, offline. No filter, sort, paging, scoring, enrichment or crawl options.

* Leads come from the Phase 10 query (`query_stored_leads` with the default `LeadQuery()`): every stored lead, domain order, same as the exports.
* Output: no leads -> `No leads found.` (exit 0). Otherwise a header line and one tab-separated line per lead: `business_name, domain, website, email_count, phone_count, first_seen, last_seen`. Missing values are `-`; timestamps are ISO 8601 UTC. Text is shown with control characters and whitespace runs collapsed to one space (display only; stored data is untouched); Persian text and ZWNJ are kept.
* A database without a `leads` table exits 1 with a message to run `db upgrade`.
* Library API: `list_stored_leads(repository) -> list[LeadRow]`, `format_lead_listing(rows)`. Tests: `tests/test_lead_listing.py`.

## Lead search (Phase 11.2)

`leadfinder leads search` (CLI; `app/cli/main.py`) filters the stored leads with the Phase 10.3 `LeadFilter`; rows and text come from `app/leads/listing.py` (`list_stored_leads(repository, flt=None)`). Read-only, offline. No new filtering logic, no sorting/paging/scoring/enrichment options.

* Options: `--domain`, `--language`, `--country`, `--city`, `--page-type` (each repeatable: repeated values are alternatives, OR), `--has-email/--no-email`, `--has-phone/--no-phone`, `--has-social-profile/--no-social-profile`. Different options combine with AND. No option lists every lead (same output as `leads list`).
* Matching, normalisation (case folding, Arabic/Persian letter forms, canonical domain) and exactness are exactly those of 10.3.
* Output is the 11.1 listing. No match: `No leads match the filters.` (exit 0); empty database without filters: `No leads found.`
* Invalid values (blank text, unknown page type with the valid ones listed, unusable domain) print `invalid filter: <message>` and exit 2, before the database is opened. Unknown options are usage errors (exit 2). A database without a `leads` table exits 1.
* Tests: `tests/test_lead_search.py`.

## Lead export interface (Phase 11.3)

`leadfinder leads export` (CLI; `app/cli/main.py`) with the pure parts in `app/leads/export_command.py`. Read-only, offline. No new export format and no new selection logic.

* `--format/-f json|csv` (default `json`, case-insensitive). The bytes are exactly the Phase 10.1 JSON document / Phase 10.2 CSV of the selected leads (domain order, UTF-8, Persian and ZWNJ unescaped, RFC 4180 escaping, CRLF in CSV), written without newline translation. Identical data gives identical bytes.
* Selection: the filters of `leads search` (`--domain`, `--language`, `--country`, `--city`, `--page-type` repeatable; `--has-email/--no-email`, `--has-phone/--no-phone`, `--has-social-profile/--no-social-profile`), read through the Phase 10 query. No filter exports every lead.
* Output: standard output, or `--output/-o FILE` (atomic replace; prints `wrote N lead(s) to FILE`). `--excel-bom` (CSV only) prepends a UTF-8 BOM so Excel reads Persian text.
* Empty database or no match: a valid empty export (`{"version": 1, "count": 0, "leads": []}` / the CSV header row), exit 0.
* Errors: unknown format, `--excel-bom` with JSON, or an invalid filter value exit 2 before the database is opened or a file is touched; a database without a `leads` table, or an unwritable output path, exits 1.
* Library API: `export_format`, `validate_export`, `select_leads`, `render_export`, `write_export`, `LeadExportError`, `EXPORT_FORMATS`. Tests: `tests/test_lead_export_interface.py`.

## Lead output controls (Phase 11.4)

`leadfinder leads list` and `leadfinder leads search` accept sorting and pagination options. They use the Phase 10 query (`LeadQuery`: 10.3 filter, then 10.4 sort, then 10.4 page); nothing is re-implemented, and no ranking or scoring exists. Read-only, offline.

* `--sort-by business_name|domain|first_seen|last_seen` (default `domain`), `--desc/--asc` (default ascending). Leads without a value for the field (no name, no timestamp) come last in both directions. Ties are broken by canonical domain, ascending in both directions, so the order never depends on storage order.
* `--page N` (from 1) and `--page-size N` (1-1000, default 50). Giving either turns paging on; the other defaults. Without either, every matching lead is listed, as before. With `search`, filtering happens first, then sorting, then paging.
* Output: the unchanged 11.1 listing for the selected leads. A paged, non-empty result also writes `page P of T (n of TOTAL lead(s), page size S)` to standard error, so standard output stays parseable. A page beyond the last one is not an error: standard output is `No leads on page P (TOTAL lead(s) in T page(s) of S).`, exit 0. No leads at all: `No leads found.` / `No leads match the filters.`
* Errors (exit 2, before the database is opened): `invalid paging: page: must be at least 1`, `page_size: must be at most 1000`, `sort_by: unknown field 'x' (valid: ...)`. A non-numeric `--page` / `--page-size` is a usage error (exit 2). A database without a `leads` table exits 1 for valid requests.
* Library API: `list_leads_page(repository, flt, sort_by, descending, page, page_size) -> LeadListing` (`rows`, `total`, `page`, `page_size`, `total_pages`, `empty_message`, `summary`). Invalid arguments raise `LeadPagingError` before any read.
* Not included: `leads export` is unchanged (no sort/page options). Tests: `tests/test_lead_output_controls.py`.


## Lead management interface (Phase 11.5)

`leadfinder leads list | search | export` form one interface over the Phase 10 query:

    stored leads -> filter (10.3) -> sort (10.4) -> paginate (10.4) -> display (list/search) or export (export: JSON 10.1 / CSV 10.2)

* `leads export` accepts `--sort-by business_name|domain|first_seen|last_seen`, `--desc/--asc`, `--page`, `--page-size` like `list`/`search`. The file holds exactly the selected leads in query order; paging numbers are not written into it. Without these options the output is the unchanged Phase 11.3 export.
* Invalid format, filter or paging values exit 2 before the database is opened or a file is written. A page beyond the last, or no match, is a valid empty export.
* Read-only (SELECT only), no network, UTF-8 (Persian and ZWNJ kept); identical data and options give identical bytes.
* `app.leads.select_lead_page(...)` is the pure entry point behind the export command.

## Website metadata (Phase 12.1)

* `WebsiteMetadata` (`app/leads/metadata.py`) maps what the HTML parser already extracted: `title`, `description`, `keywords`, `favicon_url`, `canonical_url`, `og_title`, `og_description`, `og_image`, `og_url`, `twitter_card`, `twitter_title`, `twitter_description`, `twitter_image`. Every field is optional (`None`); no favicon is guessed when the page declares none.
* URL fields are resolved against the page URL and normalised with `normalize_url` (any port, like `BusinessLead`); a value that is not a usable http(s) URL becomes `None`. Text is whitespace-collapsed, `twitter_card` is lower-cased.
* Stored in `leads.website_metadata` (JSON, migration `0006`) via `LeadRepository.merge_metadata`; read with `load_metadata`. `LeadCollector` merges each fetched HTML page's metadata after the lead. Across pages of one site the stored value of each field wins and a page only fills missing fields, so nothing is duplicated, repeated pages change nothing and the result is deterministic (the first page that supplies a field decides it).
* `BusinessLead`, `to_dict()`, the JSON/CSV exports, filters and listings do not include the metadata.
