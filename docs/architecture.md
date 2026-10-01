# Architecture (Phase 1)

- `app/main.py` — `create_app()` builds the FastAPI app, configures logging, registers routers, logs startup/shutdown via lifespan.
- `app/core/config.py` — `Settings` (pydantic-settings) with cached `get_settings()`.
- `app/core/logging.py` — stdlib logging with a JSON formatter; no external dependencies.
- `app/api/health.py` — `GET /health`.
- `app/cli/main.py` — Typer CLI: `version`, `config`, `serve`.

- `app/db/` — `Base` (naming convention, id/timestamp mixins), lazy engine/session helpers (SQLite FK + transaction handling), `uow.py` (UnitOfWork), `migrate.py` (Alembic API).
- `app/repositories/` — one repository per model over a generic `BaseRepository`; flush-only, caller commits.
- `app/core/security.py` — API key generation/hashing.
- `alembic/` — migration environment and versions.
- `app/models/` — ten SQLAlchemy 2 models plus enums; see `docs/database.md`.
- `app/urls/` — URL engine and crawl policy (normalisation, scope, SSRF, content eligibility, trap detection, canonical/duplicate handling); pure logic, no I/O. See `docs/urls.md`.
- `app/discovery/` — Phase 5 (5.1-5.4, complete) sitemap discovery: robots.txt, `Sitemap:` extraction, `/sitemap.xml` fallback, `<urlset>` and recursive `<sitemapindex>` traversal (visited-set cycle detection, max depth), policy-gated sitemap and page URLs, DTD-refusing streaming XML parser, configurable depth/file/URL/size limits; network only via an injectable fetcher. See `docs/discovery.md`.
- `app/crawler/` — Phase 6.1 async HTTP client foundation: shared `httpx.AsyncClient`, manual validated redirects, size-capped streaming, content-type gating, structured `PageResult`; fetching only (no BFS/link discovery). Phase 6.2 adds `links.py` (HTML link extraction) and `bfs.py` (`BfsCrawler`: level-by-level crawl through `UrlGate`, `max_pages`/`max_depth`) on top of it. Phase 6.3.1 adds `ratelimit.py` (async `RequestRateLimiter`) and the `max_crawl_time` / `request_delay` controls. Phase 7.1 adds `html_parser.py` (pure HTML parsing into `ParsedPage`: title, visible text, headings, raw links, metadata) attached to each `CrawledPage`. See `docs/crawler.md`.

Layering: API/CLI → UnitOfWork → repositories → models → database. See `docs/database.md`.
