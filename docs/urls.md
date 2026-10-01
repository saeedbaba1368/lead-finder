# URL engine and crawl policy (Phase 4)

Package: `app/urls/`. Pure logic — **no network access and no crawling**. The future crawler
calls this layer to decide which URLs may enter its frontier and which responses are worth
parsing. Every threshold is configurable through `UrlPolicy` (env: `APP_URL_*`, see
`docs/configuration.md`).

## Modules

| Module | Responsibility |
|---|---|
| `policy.py` | `UrlPolicy` — immutable, validated configuration; `UrlPolicy.from_settings()` |
| `normalize.py` | `normalize_url()` → `NormalizedUrl`; raises `UrlRejected(reason)` |
| `domains.py` | registered-domain extraction, subdomain policy, scope and blocklist |
| `domain_identity.py` | canonical website / domain identity (`domain_identity`, `canonical_domain`) |
| `ssrf.py` | host/IP blocking, resolved-address validation (injected resolver) |
| `content.py` | extension, `Content-Type` and `Content-Length` eligibility |
| `traps.py` | stateless trap detection and the stateful `TrapTracker` |
| `canonical.py` | `<link rel="canonical">` extraction and validation |
| `dedupe.py` | `dedupe_key()` and `SeenUrls` |
| `engine.py` | `UrlEvaluator` (stateless), `UrlGate` (per-crawl state), `UrlDecision` |
| `errors.py` | `RejectReason` enum and `UrlRejected` |

## Usage

```python
from app.core.config import get_settings
from app.urls import UrlEvaluator, UrlGate, UrlPolicy

policy = UrlPolicy.from_settings(get_settings())
gate = UrlGate(UrlEvaluator(policy, seed_url="example.com"))   # seed defaults to https://

decision = gate.admit("/team/?utm_source=x", base="https://example.com/")
decision.allowed, decision.url, decision.reason   # True, "https://example.com/team", None
gate.admit("/team", base="https://example.com/").reason        # RejectReason.DUPLICATE
gate.evaluator.evaluate_redirect(current_url, location_header)  # same rules for redirects
```

`UrlEvaluator.evaluate()` is stateless and safe to share. `UrlGate.admit()` additionally
remembers admitted URLs and trap counters for one crawl. Every rejection carries a
`RejectReason` and a short `detail`; the gate keeps a `Counter` of rejections by reason.

## Normalisation

Applied in `normalize_url`, in this spirit: URLs that identify the same resource compare equal.

* Trim whitespace, drop tab/CR/LF, reject other control characters, treat `\` as `/`
  (so `http://good\@evil/` cannot make two parsers disagree about the host).
* Resolve against `base` (relative, `//host`, `?query`, `#frag`). Only `http`/`https` are
  accepted; `mailto:`, `javascript:`, `tel:`, `data:`, `ftp:`, `file:` … are rejected.
  Embedded credentials (`user:pw@host`) are rejected.
* Host: lower-case, IDNA → punycode, one trailing dot removed, percent-decoding, IPv4 in
  any legacy form (`2130706433`, `0x7f.1`, `0177.0.0.1`, `127.1`) rewritten to a dotted
  quad, IPv6 compressed in brackets. Hosts whose last label is numeric must be valid IPv4.
* Port: default port dropped; ports restricted to `APP_URL_ALLOWED_PORTS` (default 80, 443).
* Path: percent-escapes upper-cased, unreserved characters decoded (`%7E`→`~`, `%2e%2e`→`..`
  *before* dot-segment removal), non-ASCII UTF-8 encoded, `%2F` preserved, `.`/`..`
  resolved, duplicate slashes collapsed, `;jsessionid=…` removed.
* **Trailing slash:** stripped from non-root paths by default (`APP_URL_TRAILING_SLASH=keep`
  to preserve). Root is always `/`.
* **Fragment:** always removed (including `#!` hash-bangs).
* **Tracking parameters:** removed case-insensitively — `utm_*`, `pk_*`, `mtm_*`, `hsa_*`
  prefixes and an exact list (`fbclid`, `gclid`, `msclkid`, `mc_cid`, `mc_eid`, `_hsenc`,
  `igshid`, `PHPSESSID`, `jsessionid`, …). "Safe" means known analytics/session names only:
  generic names such as `ref`, `source`, `campaign`, `sid` can change page content and are
  kept unless added via `APP_URL_EXTRA_TRACKING_PARAMS`.
* Query: empty pairs dropped, remaining pairs sorted by name (stable for repeated keys).
* Normalisation is idempotent (tested, including a fuzz test).

## Domains, scope and subdomains

`registered_domain()` uses a **built-in subset** of the Public Suffix List
(`suffixes.py`): common multi-label country suffixes (including `com.az`, `co.uk`,
`com.au`, …) and hosting platforms where subdomains belong to different owners
(`github.io`, `herokuapp.com`, `vercel.app`, …). Add more with
`APP_URL_EXTRA_PUBLIC_SUFFIXES`. Hosts that *are* a public suffix are rejected
(`PUBLIC_SUFFIX`).

A crawl's scope is its seed host's registered domain plus `APP_URL_SCOPE_DOMAINS`.
`APP_URL_SUBDOMAIN_MODE`:

| Mode | In scope |
|---|---|
| `exact` | the seed host only |
| `www` | the seed host and its `www.`/apex twin |
| `relevant` *(default)* | apex/www plus subdomains whose labels are all on the allow-list (`team`, `people`, `about`, `contact`, `careers`, `blog`, `news`, `press`, `ir`, locale codes like `en`/`de`/`en-us`, `www2`, …) and none on the block-list (`mail`, `cdn`, `static`, `api`, `dev`, `staging`, `vpn`, `admin`, `ns1`, …). Blocking wins. |
| `all` | any subdomain of an in-scope registered domain |

The seed host itself is always in scope. `APP_URL_BLOCKED_DOMAINS` (and their subdomains)
are never crawled, regardless of scope. Lookalikes (`example.com.evil.com`) are out of scope.

## SSRF protection

Checked on every URL, redirect target, and canonical hint:

* Names: `localhost`, `*.localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`,
  `*.svc`, `*.cluster.local`, cloud metadata hostnames, single-label hosts, and public
  wildcard-DNS services that map names to arbitrary IPs (`nip.io`, `sslip.io`, `lvh.me`, …).
* Addresses: loopback, private (RFC 1918 / ULA), link-local, CGNAT, multicast, reserved,
  documentation, benchmarking, unspecified, Teredo — plus IPv4 hidden inside IPv6
  (mapped, translated, NAT64, 6to4, IPv4-compatible).
* Cloud metadata addresses (`169.254.169.254`, `169.254.170.2`, `100.100.100.200`,
  `168.63.129.16`, `192.0.0.192`, `fd00:ec2::254`) are blocked **even when**
  `APP_URL_SSRF_ALLOW_PRIVATE_NETWORKS=true`. That flag is for local development only and
  is refused when `APP_ENV=production`.
* Because IP literals are canonicalised during normalisation, obfuscated forms
  (`http://2130706433/`, `http://0x7f.1/`, `http://[::ffff:127.0.0.1]/`) are caught.

**DNS layer:** a public-looking name can resolve to an internal address. Use
`resolve_and_check(host, resolver, policy)`; it validates *every* returned address and
returns them so the fetcher can connect to a validated IP (avoiding DNS-rebinding races).
The resolver is injected; `system_resolver` performs real DNS and is not used by tests.
The Phase 4 engine never resolves names itself.

## Content eligibility

* `check_extension(path, policy)` rejects images, audio/video, archives, binaries, CSS/JS,
  JSON/XML/feeds, fonts, office documents, `.ics`, `.vcf`, `.csv`. PDF and `.txt` are opt-in
  (`APP_URL_ALLOW_PDF`, `APP_URL_ALLOW_PLAIN_TEXT`). Extension-less and `.php/.aspx/.jsp`
  paths are allowed.
* `check_content_type(header, policy)` accepts `text/html` and `application/xhtml+xml`
  (plus opt-ins). A missing header is accepted by default (`allow_missing_content_type`).
* `check_content_length(value, policy)` rejects declared sizes above `max_content_length`
  (10 MiB). The fetcher will still need a streaming size cap.

## Trap detection

Stateless (single URL): path depth; back-to-back repeating path cycles of 1–3 segments
(`/a/a/a`, `/a/b/a/b/a/b`); query-parameter count; pagination beyond
`APP_URL_MAX_PAGE_NUMBER` (`?page=`, `?paged=`, `/page/N`, `/page-N`) or a huge `?offset=`;
calendar/archive listings whose year is outside `[today − back, today + ahead]`
(`/events/2035/05`, `?date=2001-01-01`, `?ym=202605`). Blog *post* URLs
(`/2012/05/my-post`) are not treated as calendars; blog *archive* listings outside the year
window are dropped.

Stateful (`TrapTracker`, per crawl): at most N distinct query strings per path
(page numbers don't count as variants), at most N URLs per calendar-style pattern (numbers
masked), and `record_empty_page(url)` marks a listing exhausted so later pages are rejected.
Duplicates never consume trap budget.

## Canonical URLs

`extract_canonical_href(html)` reads the first `<link rel="canonical">` in `<head>` (bounded
input). `resolve_canonical(page_url, href, policy)` normalises it against the page and
**ignores** hints that are invalid, unsafe (SSRF), cross-domain (unless allowed), point a
deep page at the site root (`ignore_root_canonical`), or drop the pagination of page ≥ 2.
`UrlGate.record_canonical()` also registers the canonical for duplicate detection and reports
`duplicate_of_seen` when another URL already covers it.

## Duplicate detection

`dedupe_key()` collides URLs that serve the same page: identical after normalisation, ignoring
scheme, a leading `www.`, and trailing index documents (`/team/index.html` ≡ `/team`). Paths
stay case-sensitive. The key is for comparison only; the URL that is fetched remains the
normalised one.

## Canonical domain identity (Phase 9.1)

`app/urls/domain_identity.py`: `domain_identity(value)` -> `DomainIdentity(domain, host, website, scheme, port, path)` and `canonical_domain(value)` (the `domain` alone). Pure; no network, clock or configuration. `value` may be a full URL, a scheme-less URL (`Acme.example/team`, scheme `default_scheme`, `https` unless given; `default_scheme=None` is strict), a protocol-relative URL, a bare host or a bare IPv6 address. Unusable values raise `UrlRejected`.

* **`website`** is the canonical URL (`normalize_url`, any port): lower-case scheme and host, punycode host, trailing host dot, default port and fragment removed, tracking parameters removed, query sorted, dot segments / duplicate slashes / trailing slash normalised. **The path is kept** (`https://Acme.example/en/team/` -> `https://acme.example/en/team`) and so is a leading `www.` (it is the address that is fetched). `http` is not upgraded to `https`.
* **`domain`** is the identity key: the registrable domain (eTLD+1) of the host. Scheme, port, path, query, fragment and every subdomain, `www.` included, never take part, so all spellings of a site agree. `www.` is never part of an identity, also for public suffixes (`www.co.uk` -> `co.uk`, `www.github.io` -> `github.io`); a real domain that starts with `www` (`www.com`) is left alone. IP literals are their own identity (IPv4 dotted quad; IPv6 compressed **without** brackets, as stored lead domains always were); so are single-label hosts and hosts that are a public suffix. Hosting-platform tenants (`a.github.io`, `b.github.io`) stay separate.
* The crawl port policy is not applied (a website is data). Extra public suffixes are opt-in (`extra_suffixes=`); the default never depends on configuration.
* This is only the identity. Comparing or de-duplicating leads, and lookups by identity, are not part of it.
* Known quirk of `normalize_url` (unchanged, see `PHASE_REPORT_9_1.md`): with `default_scheme` set, an *upper-case* scheme such as `HTTP://X` is taken for a host. `domain_identity` avoids this itself.

## Known limitations

* Public-suffix data is a hand-maintained subset, not the full list.
* Heuristics are heuristics: a legitimate site with >50 result pages, or archives older than
  five years that matter to the user, needs the thresholds raised.
* No content-based duplicate detection (needs fetched bodies — crawler phase).
* `robots.txt`, rate limiting, redirect-following and DNS resolution belong to the crawler.
