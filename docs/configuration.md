# Configuration

Settings are read from environment variables (prefix `APP_`) and an optional `.env` file.

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `leadfinder` | Application name |
| `APP_ENV` | `development` | `development`, `test`, or `production` |
| `APP_DEBUG` | `false` | FastAPI debug mode |
| `APP_HOST` | `127.0.0.1` | Bind host for `serve` |
| `APP_PORT` | `8000` | Bind port (1-65535) |
| `APP_LOG_LEVEL` | `INFO` | Standard logging level |
| `APP_LOG_JSON` | `true` | JSON log lines (`false` = human-readable) |
| `APP_DATABASE_URL` | `sqlite:///./leadfinder.db` | SQLAlchemy URL (e.g. `postgresql+psycopg://user:pass@host/db`) |
| `APP_DB_ECHO` | `false` | Log SQL statements |
| `APP_URL_MAX_LENGTH` | `2048` | Longest accepted URL (64-65536) |
| `APP_URL_ALLOWED_PORTS` | `80,443` | Allowed ports, comma-separated; `*` or empty allows any |
| `APP_URL_SUBDOMAIN_MODE` | `relevant` | `exact`, `www`, `relevant`, or `all` (see `docs/urls.md`) |
| `APP_URL_SCOPE_DOMAINS` | *(empty)* | Extra registered domains treated as in scope |
| `APP_URL_BLOCKED_DOMAINS` | *(empty)* | Domains (and subdomains) that are never crawled |
| `APP_URL_EXTRA_TRACKING_PARAMS` | *(empty)* | Additional query parameters to strip |
| `APP_URL_EXTRA_PUBLIC_SUFFIXES` | *(empty)* | Additional public suffixes for registered-domain logic |
| `APP_URL_TRAILING_SLASH` | `strip` | `strip` or `keep` trailing slashes on non-root paths |
| `APP_URL_ALLOW_PDF` | `false` | Treat PDFs as eligible content |
| `APP_URL_ALLOW_PLAIN_TEXT` | `false` | Treat `text/plain` / `.txt` as eligible content |
| `APP_URL_SSRF_ALLOW_PRIVATE_NETWORKS` | `false` | Dev/test only. Refused when `APP_ENV=production`. Never unblocks cloud-metadata addresses |
| `APP_URL_MAX_PATH_DEPTH` | `12` | Maximum path segments |
| `APP_URL_MAX_QUERY_PARAMS` | `12` | Maximum query parameters |
| `APP_URL_MAX_PAGE_NUMBER` | `50` | Highest page number followed in `?page=N` / `/page/N` |
| `APP_URL_MAX_QUERY_VARIANTS_PER_PATH` | `30` | Distinct query strings per path, per crawl |
| `APP_URL_MAX_CALENDAR_URLS_PER_PATTERN` | `40` | URLs per calendar-style pattern, per crawl |
| `APP_URL_CALENDAR_YEARS_BACK` | `5` | Calendar/archive years older than this are traps |
| `APP_URL_CALENDAR_YEARS_AHEAD` | `1` | Calendar/archive years further ahead are traps |
| `APP_DISCOVERY_TIMEOUT_SECONDS` | `10` | Per-request timeout for robots.txt / sitemap fetches (>0, at most 120) |
| `APP_DISCOVERY_MAX_BYTES` | `5242880` | Largest robots.txt / sitemap body accepted (1024-104857600) |
| `APP_DISCOVERY_MAX_REDIRECTS` | `3` | Redirects followed per fetch; each hop is re-validated by the URL policy (0-10) |
| `APP_DISCOVERY_MAX_DEPTH` | `5` | Sitemap-index nesting depth; top-level sitemaps are depth 0 (0-20) |
| `APP_DISCOVERY_MAX_SITEMAPS` | `1000` | Sitemap files fetched per discovery run, robots.txt excluded (1-100000) |
| `APP_DISCOVERY_MAX_URLS` | `100000` | Page URLs admitted per discovery run; no further sitemap is fetched once reached (1-5000000) |
| `APP_DISCOVERY_USER_AGENT` | `leadfinder/0.5 (+sitemap-discovery)` | `User-Agent` header sent by discovery |
| `APP_CRAWLER_CONNECT_TIMEOUT` | `10` | Seconds to establish a connection (>0, at most 120) |
| `APP_CRAWLER_READ_TIMEOUT` | `15` | Seconds to wait for response data (>0, at most 300) |
| `APP_CRAWLER_WRITE_TIMEOUT` | `10` | Seconds to send the request (>0, at most 120) |
| `APP_CRAWLER_POOL_TIMEOUT` | `10` | Seconds to wait for a free pooled connection (>0, at most 120) |
| `APP_CRAWLER_FOLLOW_REDIRECTS` | `true` | `false` reports a 3xx (`redirect_not_followed`) instead of following it |
| `APP_CRAWLER_MAX_REDIRECTS` | `5` | Redirect hops followed per fetch (0-20) |
| `APP_CRAWLER_MAX_RESPONSE_BYTES` | `5242880` | Largest body accepted, checked on `Content-Length` and while streaming (1024-209715200) |
| `APP_CRAWLER_SUPPORTED_CONTENT_TYPES` | `text/html,application/xhtml+xml` | Comma-separated media types whose bodies are read |
| `APP_CRAWLER_ALLOW_MISSING_CONTENT_TYPE` | `true` | Accept 2xx responses without a `Content-Type` |
| `APP_CRAWLER_USER_AGENT` | `leadfinder/0.6 (+http-crawler)` | `User-Agent` header sent by the HTTP client |
| `APP_CRAWLER_MAX_CONNECTIONS` | `20` | Connection pool size (1-1000) |
| `APP_CRAWLER_MAX_KEEPALIVE_CONNECTIONS` | `10` | Idle connections kept open for reuse (0-1000) |
| `APP_CRAWLER_MAX_PAGES` | `100` | Pages fetched per crawl; failed fetches count (1-100000) |
| `APP_CRAWLER_MAX_DEPTH` | `3` | Link depth from the seed, seed = 0; links on pages at this depth are not followed (0-50) |
| `APP_CRAWLER_MAX_CRAWL_TIME` | `0` | Maximum crawl runtime in seconds, measured on a monotonic clock (unaffected by system clock changes); `0` = no limit (the constructor rejects `0`/negative; `None` means no limit). No request starts after it is used up, and the request in flight is cancelled (0-86400) |
| `APP_CRAWLER_REQUEST_DELAY` | `0` | Minimum seconds between request starts (asynchronous, never a blocking sleep); `0` = no delay (0-3600) |

Invalid values fail fast at startup with a validation error.
