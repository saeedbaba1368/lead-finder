# HTTP crawler (Phase 6.1 client, Phase 6.2 BFS)

Package: `app/crawler/`. Phase 6.1 is the async HTTP fetching layer (`http_client.py`, `models.py`); Phase 6.2 adds internal-link discovery (`links.py`) and a breadth-first crawler (`bfs.py`) on top of it. Phase 7.1 adds HTML content parsing (`html_parser.py`, documented at the end). Not implemented: Playwright, email/people extraction and other advanced extraction. Sections up to "Known limitations" describe the 6.1 client; the BFS crawler is documented at the end.

## Usage

```python
from app.core.config import get_settings
from app.crawler import AsyncHttpClient, HttpClientConfig

async with AsyncHttpClient(HttpClientConfig.from_settings(get_settings())) as client:
    page = await client.fetch("https://example.com/")
    if page.ok:
        html = page.text
```

One `httpx.AsyncClient` is created per `AsyncHttpClient` and reused for every `fetch`, so keep-alive
connections are pooled (`APP_CRAWLER_MAX_CONNECTIONS`, `APP_CRAWLER_MAX_KEEPALIVE_CONNECTIONS`).
Close it with `async with` or `await client.aclose()`. HTTP is the default fetch method.

Optionally pass `evaluator=UrlEvaluator(...)` (and a `resolver`) to run every URL and every redirect
hop through the Phase 4 URL policy and DNS/SSRF check before connecting. Without an evaluator only
the scheme/host are validated. The crawler phases that follow should always pass one.

## Result: `PageResult`

`url`, `final_url`, `outcome` (`FetchOutcome`), `status_code`, `content_type` (media type, lower-case),
`charset`, `headers` (lower-case names), `size` (bytes read), `body`, `redirects` (tuple of
`RedirectHop(url, status_code, location)`), `duration` (seconds, whole fetch), `error`
(`FetchError(kind, message, detail)`), plus `ok`, `redirected` and `text` helpers.

## Outcomes

| Outcome | Meaning |
|---|---|
| `ok` | 2xx, supported content type, body within the size limit |
| `http_error` | 4xx/5xx or any non-2xx, non-redirect status. Body is not read; status/headers are reported |
| `redirect_not_followed` | 3xx while `APP_CRAWLER_FOLLOW_REDIRECTS=false` |
| `redirect_error` | loop, more than `APP_CRAWLER_MAX_REDIRECTS` hops, missing `Location`, non-http(s) target |
| `unsupported_content_type` | `Content-Type` not in `APP_CRAWLER_SUPPORTED_CONTENT_TYPES` (body not read) |
| `too_large` | declared `Content-Length` or streamed body above `APP_CRAWLER_MAX_RESPONSE_BYTES` |
| `timeout` | connect/read/write/pool timeout |
| `connection_error` | refused, reset, DNS failure, protocol/TLS errors |
| `invalid_url` | empty, relative, non-http(s), no host, bad port |
| `blocked` | refused by URL policy / SSRF check (only with an evaluator) |
| `error` | any other unexpected exception (contained; never raised) |

`fetch` never raises for per-URL failures (only `asyncio.CancelledError` propagates).

## Redirects

httpx redirect following is disabled; the client follows redirects itself so each hop is recorded,
counted, loop-checked and re-validated. Relative `Location` values are resolved against the current URL.

## Content types

Only media types in `APP_CRAWLER_SUPPORTED_CONTENT_TYPES` (default HTML and XHTML) have their bodies
read. A missing `Content-Type` is accepted by default (`APP_CRAWLER_ALLOW_MISSING_CONTENT_TYPE`).
Size limits apply to the decoded body (so compressed bombs are capped).

## Known limitations

* No DNS pinning: the SSRF DNS check and the connection resolve separately (same as Phase 5).
* No retries, no robots.txt evaluation, no rate limiting, no cookies policy.
* Error and redirect bodies are not read, so those connections are closed rather than reused.
* HTTP/2 is not enabled.

## Phase 6.2: link discovery and BFS

```python
from app.crawler import build_crawler

async with build_crawler(get_settings(), "https://example.com") as crawler:
    result = await crawler.crawl("https://example.com/")
```

`BfsCrawler(client, evaluator, max_pages=, max_depth=)` reuses `AsyncHttpClient` unchanged (no HTTP logic is duplicated). The client and crawler should share one `UrlEvaluator` (`build_crawler` does this) so redirect targets are scope-checked too. Used as a context manager, the crawler closes the client. `crawl()` can be called repeatedly; each call starts with fresh state.

**Link extraction (`extract_links`)**: `<a href>` and `<area href>`, plus the first valid `<base href>`. Values are stripped and de-duplicated in document order. `mailto:`, `tel:`, `javascript:`, `data:`, `ftp:` and other non-http(s) schemes, empty values and fragment-only links (`#x`) are skipped and counted. Malformed HTML never raises; malformed URLs are passed on and rejected by the URL engine (counted in `rejections`). At most 10,000 hrefs are collected per page. Links are only extracted from pages that fetched OK with an HTML (or missing) content type.

**Admission**: every href is resolved against the page's final URL (after redirects) or `<base href>`, then passed through `UrlGate.admit`: normalisation (fragment removal, tracking-parameter removal, query sorting, trailing-slash policy, case), SSRF, scope, extension, trap checks and duplicate suppression. Each normalised URL is queued at most once. Only URLs in scope of the seed are followed: external registered domains, other IP hosts and blocked domains are rejected. Scope follows `APP_URL_SUBDOMAIN_MODE` / `APP_URL_SCOPE_DOMAINS`; ports are not part of scope. No new external-domain option was added.

**BFS**: depth 0 is the seed, depth 1 its links, and so on; each level is fully processed, in queue order, before the next. Requests are sequential (no concurrency); Phase 6.3.1 adds an optional delay between them.

**Limits**
* `max_depth` (`APP_CRAWLER_MAX_DEPTH`, default 3): links on pages at this depth are not followed (`depth_limited`, `skipped_by_depth`). `0` fetches only the seed. Reaching it is a normal finish (`stop_reason=completed`).
* `max_pages` (`APP_CRAWLER_MAX_PAGES`, default 100): counts fetch attempts, failed ones included. Nothing beyond it is queued or requested; if URLs were left over the crawl ends with `stop_reason=max_pages`. A crawl that fits exactly is `completed`.
* `max_crawl_time` (`APP_CRAWLER_MAX_CRAWL_TIME`, default `0` = unlimited; constructor `max_crawl_time=None`): maximum runtime measured from the start of `crawl()` on the event loop's monotonic clock (`loop.time()`, i.e. `time.monotonic()`), so system clock changes cannot shorten or extend it. `0` in settings means unlimited; the `BfsCrawler` constructor accepts `None` for unlimited and rejects `0` or negative values, and settings validation rejects negatives (`ge=0`). This is the crawl deadline, not a per-request timeout: the per-request HTTP timeouts are unchanged (`APP_CRAWLER_*_TIMEOUT`). It is checked before every request, both before and again after the rate-limit wait (so a slot that wakes at the deadline starts nothing), and the request in flight is bounded by the remaining time with `asyncio.timeout_at`: if the budget runs out mid-request, that fetch is cancelled and discarded (not listed in `pages`; its URL stays in `pending`). Nothing new is started afterwards. The crawl ends with `stop_reason=max_crawl_time`, `time_limited=True`, and `elapsed` holds the run time. A crawl that finishes its frontier inside the budget is `completed`.
* `request_delay` (`APP_CRAWLER_REQUEST_DELAY`, default `0`): minimum seconds between request **starts**, enforced by `RequestRateLimiter` (`app/crawler/ratelimit.py`): slots are reserved under an `asyncio.Lock` and awaited with `asyncio.sleep`, so the event loop is never blocked and concurrent callers are spaced out, not released in a burst. The first request is not delayed; each `crawl()` starts with a fresh limiter. The delay applies per page fetch, not per redirect hop inside one fetch (hops are bounded by `APP_CRAWLER_MAX_REDIRECTS`).
* **Combined limits**: checks run in this order before each request: `max_pages`, then the time/rate slot. The first limit that applies ends the crawl and sets its own stop reason (`max_pages` or `max_crawl_time`); `max_depth` simply stops link following and is a normal `completed` finish unless another limit hit first. If the next delay slot would fall after the deadline, the crawler stops immediately instead of sleeping until the deadline.

**Redirects**: a redirect target counts as fetched. If it was already queued under its own URL it is not fetched again (`redirect_duplicates`).

**`CrawlResult`**: `pages` (every attempt, BFS order; each `CrawledPage` has `url`, `depth`, `parent`, the untouched 6.1 `PageResult`, `links_found`, `links_queued`), `queued`, `visited`, `fetched` (outcome OK), `failed` (any other outcome: HTTP error, timeout, connection error, unsupported content type, blocked, ...), `pending`, `current_depth`, `deepest_level`, `stop_reason` (`completed` / `max_pages` / `max_crawl_time` / `seed_rejected`), `max_crawl_time`, `request_delay`, `elapsed`, `time_limited`, `depth_limited`, `skipped_by_depth`, `skipped_by_max_pages`, `rejections` (URL-policy reasons), `seed_rejection`. A failed page never stops the crawl. A seed that fails the URL policy returns `seed_rejected` with nothing fetched; `build_crawler` raises `UrlRejected` if the seed cannot even be normalised or is SSRF-blocked.

**Not implemented**: persistence, robots.txt evaluation, concurrency, canonical-URL handling (`UrlGate.record_canonical` exists but is not called), sitemap seeding, JavaScript rendering, extraction.

## Phase 7.1: HTML content parsing

`app/crawler/html_parser.py` turns a fetched HTML response into a structured `ParsedPage`. It is pure parsing (no network, no URL normalisation, no URL policy) and uses the standard library `html.parser`, so there is no new dependency.

```python
from app.crawler import parse_html, parse_response

page = parse_html("<title>Hi</title><h1>Hello</h1><a href='/x'>x</a>")   # str -> ParsedPage
parsed = parse_response(page_result)                                      # PageResult -> ParsedPage | None
```

**HTML detection** (`is_html_content_type`, `parse_response`): `text/html` and `application/xhtml+xml` (parameters and case ignored; the same set as `app.urls.content.HTML_MEDIA_TYPES`). `parse_response` returns `None` ("nothing to parse", not an error) when the fetch was not OK, the body is empty, the declared content type is not HTML, or no content type was declared and the first 2,048 bytes contain a NUL byte (binary). A missing content type with a text body is parsed, matching the crawler's existing treatment of missing types.

**Encoding** (`decode_html`): byte-order mark, then the HTTP charset, then a `<meta charset>` / `http-equiv` label in the first 4 KiB, then UTF-8. A label that is unknown or cannot decode the bytes is skipped; the last resort is UTF-8 with replacement characters. Persian, English, mixed text, ZWNJ (U+200C), Persian/Arabic-Indic digits and HTML entities are preserved. (`PageResult.text`, used by `extract_links`, is unchanged.)

**`ParsedPage`** (frozen dataclasses; missing values are `None` / empty, like `Page.title`):

| Field | Content |
|---|---|
| `title` | first `<title>`, whitespace-normalised; `None` if missing or blank. An unclosed `<title>` ends at the next tag |
| `text` | visible text: `script`, `style`, `noscript` and `template` content is excluded; block elements (`p`, `div`, `li`, `tr`, headings, `br`, ...) become lines, inline elements do not split words; whitespace runs collapse to one space, empty lines are dropped. Characters are otherwise untouched |
| `headings` | `Heading(level, text)` for `h1`-`h6` in document order; blank headings skipped; an unclosed heading ends at the next block-level element |
| `links` | raw `<a href>` values, stripped, de-duplicated by exact text, document order, capped at `MAX_LINKS_PER_PAGE` (10,000). **Not** resolved, normalised, scheme-filtered or scoped: `mailto:`, `#x`, relative and external hrefs are all reported as written. `<area>` and links inside hidden elements are not included |
| `metadata` | `PageMetadata(description, keywords, canonical_url, robots, og_title, og_description, og_url)`; names are case-insensitive, first non-blank occurrence wins, `og:*` accepted via `property` or `name`, `canonical_url` is the raw `href` of `<link rel~="canonical">` |

`ParsedPage.to_dict()` gives a JSON-ready dict. Malformed markup never raises: if `html.parser` itself fails, what was collected is returned and `html_parse_partial` is logged. `parse_html` raises `TypeError` only for non-`str` input (a programming error).

**Crawler integration** (`bfs.py`): after a page fetches OK as HTML, `BfsCrawler._visit` calls `parse_response` and attaches the result as `CrawledPage.parsed` (`None` for non-HTML, failed, empty or unparsable pages). A parser exception is caught, logged as `html_parse_failed` and leaves `parsed=None`; the request is still a successful fetch and the crawl continues exactly as before. Link discovery is unchanged: the crawler still uses `extract_links` + `UrlGate` (so `ParsedPage.links` is *not* what gets queued). The page title is written to the existing `pages.title` column when a page repository is configured (no schema change, no migration). `max_pages`, `max_depth`, `max_crawl_time`, `request_delay`, persistence, resume and lifecycle are untouched.

**Not implemented (later phases)**: text from `<title>`-less sources such as `og:title` fallbacks, content hashing, language detection, contact/email/phone extraction, SEO scoring, lead qualification, AI extraction, JavaScript rendering. The parsed text/headings/metadata are not persisted (only the title is).

## Page analysis pipeline (Phase 7.3.4)

For every page fetched OK as HTML the crawler runs, in one pass over the body (`BfsCrawler._visit` -> `parse_response` -> `parse_html`):

HTTP response -> HTML parsing -> contact extraction (emails, phones, social links) -> structured data (JSON-LD, addresses) -> language detection -> page classification (with the response's final URL) -> content metrics -> existing persistence/result handling.

The combined result is `CrawledPage.parsed` (`ParsedPage`). Each stage runs once per page; a failing stage degrades to its neutral value (`unknown` language, `other` classification, zero metrics) and never stops the crawl. Link discovery (`extract_links` + `UrlGate`), BFS order, `max_pages`, `max_depth`, `max_crawl_time`, `request_delay`, persistence (only `pages.title`), resume, cancellation and lifecycle are unchanged, and the analysis issues no requests. Covered by `tests/test_page_analysis_integration.py`.

## Page observer (Phase 8.5)

`BfsCrawler(..., page_observer=callable)` where `callable(page: CrawledPage, crawl_id: int) -> None` (`PageObserver`). Used with a `page_repository` only (like persistence itself). For every completed fetch the crawler stores the page row, then runs the observer in a nested SAVEPOINT, then updates the crawl progress, all inside the page's SAVEPOINT; `commit_checkpoints=True` then commits it.

- A `SQLAlchemyError` raised by the observer propagates: the page's SAVEPOINT is rolled back (the page stays `pending`, so it is requested again on resume) and a `CrawlPersistenceError` is raised, like any other failed page write.
- Any other exception is logged (`page_observer_failed`) and only the observer's own writes are rolled back; the page is stored and the crawl continues. `asyncio.CancelledError` is never swallowed.
- Not called for a frozen (already completed) crawl, for URLs that stay pending, or when no page repository is configured. With `page_observer=None` (the default) nothing changes. Fetching, link discovery, `max_pages`, `max_depth`, `max_crawl_time`, `request_delay`, resume, cancellation and lifecycle are untouched.

The lead creation observer is `app.leads.LeadCollector` (`docs/leads.md`).

