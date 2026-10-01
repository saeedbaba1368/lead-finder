# Sitemap discovery (Phase 5 — complete)

Package: `app/discovery/`. Phase 5.1 added robots.txt and basic `<urlset>` discovery; Phase 5.2
added sitemap indexes and multiple sitemap files; Phase 5.3 made index traversal recursive with
cycle detection, a depth limit and larger resource limits; Phase 5.4 audited and hardened all of it
(XML parser, network efficiency, 429 handling, end-to-end tests) and closes Phase 5. It performs network I/O (only
through an injectable `Fetcher`) and sends every URL through the Phase 4 URL policy.

## Scope

**Supported**

* robots.txt fetching and parsing; `Sitemap:` directives (any number, independent of user-agent groups)
* `/sitemap.xml` fallback when no usable declaration exists
* sitemap `<urlset>` (page URLs) and `<sitemapindex>` (child sitemaps), decided by the root element
* multiple sitemap files, nested indexes to `max_depth`, recursive depth-first traversal
* cycle detection and duplicate-sitemap suppression (each canonical sitemap is fetched once)
* URL normalisation, de-duplication and the full Phase 4 URL policy on every sitemap and page URL
* isolation of malformed / empty / unknown-root / oversize / failing sitemaps
* resource limits: depth, sitemap files, page URLs, bytes per body, redirects, per-request timeout
* deterministic tests: fake fetcher and loopback HTTP server, no DNS, no internet

**Not supported — Phase 5 is sitemap / URL discovery only.** It is **not** the web crawler. It does
**not** implement arbitrary page crawling, HTML fetching or extraction, content indexing, search-engine
indexing, JavaScript rendering or browser automation. It returns a list of policy-approved page URLs
and never fetches those pages. Those belong to future phases.

```text
Target
 ↓
robots.txt
 ↓
Sitemap declarations   (none usable → /sitemap.xml fallback)
 ↓
sitemap  (type decided by the XML root element, not the file name)
 ├── urlset → page URLs → existing URL policy (UrlGate)
 │
 └── sitemapindex
       ↓
       child sitemap ── urlset → page URLs
       ↓
       nested sitemap ─ sitemapindex → … (any depth up to max depth)
                       └ urlset → page URLs
```

## Modules

| Module | Responsibility |
|---|---|
| `fetcher.py` | `FetchResult`/`FetchStatus`, `Fetcher` callable type, stdlib `HttpFetcher` (timeout, size cap, validated redirects, DNS/SSRF check; never raises) |
| `robots.py` | `parse_robots()` → `Sitemap:` URLs plus User-agent/Allow/Disallow groups |
| `sitemap.py` | `parse_sitemap()` → `<loc>` values from a `<urlset>`, as a status, never an exception |
| `service.py` | `SitemapDiscovery.discover(target)` → `DiscoveryResult` |
| `factory.py` | `build_discovery(settings, seed_url)` |

```python
from app.core.config import get_settings
from app.discovery import build_discovery

result = build_discovery(get_settings(), "example.com").discover("example.com")
result.urls            # admitted, normalised, de-duplicated page URLs
result.robots_status   # FetchStatus.OK / NOT_FOUND / FORBIDDEN / SERVER_ERROR / TIMEOUT / ...
result.used_fallback   # True when /sitemap.xml was tried instead of declared sitemaps
result.rejections      # Counter[RejectReason] for URLs the policy refused
```

## Behaviour

* **robots.txt** is fetched once from the target origin. 200, 404/410, 401/403, 5xx, other
  statuses, timeout, connection failure, oversize, blocked target, malformed and empty bodies
  are all handled; none raises. Only a 200 body is parsed.
* **Parsing** is line-based and lenient (comments, BOM, CRLF, case-insensitive names).
  `Sitemap:` lines are independent of user-agent groups. Empty values, non-http(s) values and
  non-URLs are ignored; duplicates are dropped (and again after normalisation). Allow/Disallow
  are recorded but **not evaluated**.
* **Declared sitemap URLs** must pass the URL policy (scheme, SSRF, scope, ports). A
  declaration pointing at another registered domain, or at an internal address, is never fetched.
* **Fallback:** `/sitemap.xml` is fetched only if no usable declaration exists (robots.txt
  missing, failed, empty, malformed, without valid `Sitemap:` lines, or with declarations that
  were all refused by the URL policy). It is not fetched when a usable declaration exists.
* **HTTP statuses:** 200 → parsed; 404/410 `NOT_FOUND`; 401/403 `FORBIDDEN`; 429 `RATE_LIMITED`;
  5xx `SERVER_ERROR`; other codes `HTTP_ERROR`. There are no retries: a failing or throttled
  sitemap is recorded and skipped, and the run continues.
* **`<urlset>` parsing** accepts the standard namespace or none, ignores unknown elements
  (`lastmod`, `image:*`, `xhtml:link`, …), counts empty `<loc>` values, and caps entries at
  50,000 per file.
* **Page URLs** go through `UrlGate.admit()` (relative `<loc>` values resolve against the
  sitemap URL). Rejections are counted per `RejectReason`; duplicates use the project's
  `dedupe_key`, across all sitemaps of one discovery run.
* **Metadata vs pages:** robots.txt and sitemap XML would be refused by the page-extension
  rule (`.txt`, `.xml`), so `UrlEvaluator.evaluate/evaluate_redirect/evaluate_normalized`
  gained a keyword `check_extension=True`. Discovery passes `False` when fetching metadata only;
  every other check (SSRF, scope, ports, traps) still applies and discovered page URLs keep
  the extension check.

## XML safety

* **Parser:** `parse_sitemap()` drives Python's expat parser directly (streaming; no tree, no
  recursion, so deeply nested documents cannot exhaust the stack). Handlers refuse every DTD
  feature *inside the parser*: `<!DOCTYPE`, entity declarations, unparsed entities and external
  entity references raise, and parameter-entity parsing is disabled. External entity resolution,
  entity expansion (billion laughs) and DTD retrieval are therefore impossible, whatever the
  input encoding. The parser never opens a file or a URL.
* **Defence in depth:** a byte/UTF-16 pre-check still returns `SitemapParseStatus.UNSAFE`
  before parsing for obvious DOCTYPE/ENTITY payloads.
* Bodies over the size limit are rejected (`TOO_LARGE`) — by `Content-Length` and by bytes read.
  Responses are never decompressed, so there is no decompression-bomb path.
* Empty, malformed, binary, unknown-encoding and deeply nested input yield `EMPTY`/`MALFORMED`
  statuses. A bad sitemap never stops the remaining sitemaps. Unexpected roots yield `UNSUPPORTED`.
* Regression tests: `tests/test_discovery_phase5_e2e.py` (a sitemap whose external entity points at
  a canary URL on the test server; the canary is never requested) and `tests/test_discovery_sitemap.py`.

## Configuration

All settings use the `APP_` prefix (see also `docs/configuration.md`, `.env.example`). URL rules use
the existing `APP_URL_*` settings. `SitemapDiscovery(...)` takes the limits as keyword arguments;
`build_discovery()` wires them from settings.

| Setting | Default | Range | Meaning | Safety purpose |
|---|---|---|---|---|
| `APP_DISCOVERY_MAX_DEPTH` | 5 | 0-20 | Index nesting depth; top-level sitemaps are depth 0 | bounds recursion and crawl-of-crawl chains; keeps the Python stack safe |
| `APP_DISCOVERY_MAX_SITEMAPS` | 1000 | 1-100000 | Sitemap files fetched per run (robots.txt excluded) | bounds requests to a host |
| `APP_DISCOVERY_MAX_URLS` | 100000 | 1-5000000 | Page URLs admitted per run; no sitemap is fetched once it is spent | bounds memory and result size |
| `APP_DISCOVERY_MAX_BYTES` | 5242880 | 1024-104857600 | Largest robots.txt / sitemap body accepted | bounds memory and parse time |
| `APP_DISCOVERY_TIMEOUT_SECONDS` | 10 | >0, ≤120 | Per-request socket timeout | stops a hung server blocking discovery |
| `APP_DISCOVERY_MAX_REDIRECTS` | 3 | 0-10 | Redirects followed per fetch; every hop re-validated | bounds redirect chains, blocks SSRF via redirect |
| `APP_DISCOVERY_USER_AGENT` | `leadfinder/0.5 (+sitemap-discovery)` | non-empty | `User-Agent` header | identifies the client to site operators |

Built-in per-file cap (not configurable): 50,000 `<loc>` entries per sitemap (sitemaps.org limit);
extra entries are ignored and `SitemapOutcome.truncated` is set.

## Indexes, multiple sitemaps and recursion (Phases 5.2-5.3)

* **Type from the root element.** `<urlset>` yields page URLs; `<sitemapindex>` yields child
  sitemap URLs. `SitemapParseResult.kind` is `URLSET` or `INDEX`; any other root is `UNSUPPORTED`.
  Entries of the wrong kind, unknown elements, `<url>`/`<sitemap>` entries without `<loc>`
  (`missing_locs`) and empty `<loc>` values (`empty_locs`) are skipped, not fatal.
* **All declarations are processed**, in robots.txt order. Each top-level source is independent.
  Mixed sources (an index plus plain sitemaps) are combined into one de-duplicated page list.
* **Recursive traversal (5.3).** Any index may reference another index, to any depth up to
  `max_depth`. Traversal is depth-first in document order (`SitemapDiscovery._process` /
  `_follow_index`). Top-level sitemaps (declared or fallback) are depth 0, their children depth 1, and
  so on; `SitemapOutcome.depth` and `DiscoveryResult.max_depth_seen` record it.
* **Maximum depth.** Default 5 (`APP_DISCOVERY_MAX_DEPTH`, 0-20; 0 = read declared sitemaps but
  follow no index children). A child that would sit deeper than the limit is **not fetched**; the
  index is marked `depth_limited`, `DiscoveryResult.depth_limit_reached` is set,
  `skipped_by_depth` counts the references, and a `sitemap_depth_limit` log line is emitted.
  Python recursion depth equals sitemap depth, hard-capped at 20, so the stack cannot be exhausted.
* **Cycle detection.** A visited set keyed by the project's `dedupe_key` (the same normalisation
  used for page URLs: host case, fragment, trailing slash, tracking parameters, `www`, scheme) is
  checked before every fetch. A → B → A and A → B → C → A stop at the repeat reference, which is
  counted in `duplicate_sitemap_refs` and logged as `sitemap_already_visited`. Canonical variants
  (`HTTPS://EXAMPLE.com/a.xml#x` vs `https://example.com/a.xml`: scheme/host case, fragment, tracking
  parameters, `www`, trailing slash) count as the same sitemap. Path case is significant (`/A.xml` ≠ `/a.xml`).
* **No duplicate fetches.** Every sitemap URL (declared, fallback or child) is compared with the
  project's `dedupe_key`, so `a.xml`, `a.xml#x`, `https://EXAMPLE.com/a.xml` and an index that
  references itself are fetched once.
* **Unknown root elements** (neither `urlset` nor `sitemapindex`) yield `UNSUPPORTED`; the sitemap
  is recorded with `detail="root:<name>"` (e.g. `root:rss`), logged, and listed in `failed_sitemaps`.
* **URL policy on everything.** Declared, child, nested and page URLs all go
  through the existing policy before any request or admission. Child sitemap `<loc>` values must
  be absolute (the sitemap protocol requires it); relative ones count as `invalid_url` and are
  never fetched. Page `<loc>` values keep the 5.1 behaviour (relative ones resolve against the
  sitemap URL). Redirects of child sitemaps are re-validated by the fetcher.
* **Malformed branches.** Failures stay inside their own branch: the failing sitemap (and, if it
  was an index, only its own subtree) is skipped, and traversal continues with its siblings and with
  the other top-level sources. A child that is 404/403/5xx, times out, is oversize, is blocked, or is
  malformed/empty/unknown-root is recorded on its `SitemapOutcome` (`fetch_status`,
  `parse_status`) and logged at info level; siblings and other declarations still run.
  `DiscoveryResult.failed_sitemaps` lists them.
* **Resource limits** (conservative, configurable, see `docs/configuration.md`):

| Limit | Setting | Default | When reached |
|---|---|---|---|
| Index nesting depth | `APP_DISCOVERY_MAX_DEPTH` | 5 | deeper children not fetched; `depth_limit_reached`, `skipped_by_depth` |
| Sitemap files fetched per run | `APP_DISCOVERY_MAX_SITEMAPS` | 1000 | traversal stops; `sitemap_limit_reached`, `skipped_sitemaps` (unvisited references) |
| Page URLs admitted per run | `APP_DISCOVERY_MAX_URLS` | 100,000 | no further sitemap is fetched (top-level sources included); `url_limit_reached` |
| Bytes per robots.txt / sitemap | `APP_DISCOVERY_MAX_BYTES` | 5 MiB | sitemap reported `TOO_LARGE` |

`SitemapDiscovery(evaluator, fetcher, max_depth=…, max_sitemaps=…, max_urls=…, max_sitemap_bytes=…)` takes the
same limits directly; `build_discovery()` wires them from settings.

## Not implemented (future phases)

* Time budget / overall deadline, per-host politeness and rate limiting, byte budget across all
  sitemaps, gzip `.xml.gz` handling.
* robots Allow/Disallow evaluation, crawl-delay, persisting results, CLI/API entry points,
  connection pinning to the validated DNS address.
* The crawler itself (see **Scope** above).

## Known limitations

* `HttpFetcher` validates the DNS answer, then lets `urllib` resolve again when connecting, so a
  DNS-rebinding race is possible (same caveat noted in Phase 4). Pinning is a later task.
* `.xml.gz` sitemaps are not decompressed; they are reported as malformed.
* Limits count files and URLs, not time; the timeout is per socket operation, so a slow server can
  still take up to roughly `timeout` per read (worst case about `max_sitemaps` × timeout overall).
* `skipped_sitemaps` counts references that were seen but not fetched after the file limit was hit;
  references inside indexes that were never reached are not counted. `url_limit_reached` is also set
  when the URL budget is spent exactly and further sitemaps were left unfetched.
* Real-network fetching has not been exercised in this sandbox; only loopback HTTP servers and
  fake fetchers were used.
