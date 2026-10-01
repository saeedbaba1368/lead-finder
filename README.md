# leadfinder

A Python 3.12 FastAPI service. Phase 1 added the foundation (typed settings, structured
logging, health endpoint, CLI skeleton, offline pytest suite). Phase 2 added the SQLAlchemy 2
database models. Phase 3 adds Alembic migrations, session/transaction handling and a
repository layer (see `docs/database.md`). Phase 4 adds the URL engine and crawl policy
(`docs/urls.md`). Phase 5 (complete, 5.1-5.4) adds robots.txt / `/sitemap.xml` discovery, `<urlset>` parsing and recursive sitemap
index traversal with cycle, depth and size limits and a hardened XML parser (`docs/discovery.md`). It is
sitemap/URL discovery only: no crawler, extraction, indexing or JavaScript rendering exists yet (see
`PROJECT_RULES.md`). Phase 6.1 adds the async HTTP client (`app/crawler/`, `docs/crawler.md`); Phase 6.2 adds internal-link discovery and a breadth-first crawler with `max_pages` / `max_depth`; Phase 6.3.1 adds `max_crawl_time` and `request_delay` (async rate limiting). Phase 7.1 adds HTML content parsing (title, visible text, headings, raw links, metadata) into a `ParsedPage` for each fetched HTML page (`app/crawler/html_parser.py`). Phase 7.3.1 adds content language (`en` / `fa` / `mixed` / `unknown`) and encoding detection to `ParsedPage` (`app/crawler/language.py`). Phase 7.3.2 adds deterministic, descriptive page classification (`homepage`, `about`, `contact`, `services`, `product`, `blog`, `article`, `portfolio`, `careers`, `other`) in `app/crawler/classify.py`; it does not affect crawling. Phase 7.3.3 adds deterministic content-quality metrics (character/word/heading/link/image/email/phone counts, text-to-HTML ratio) in `app/crawler/metrics.py`. Phase 7.3.4 wires these analysis stages into the crawl pipeline (one pass per page, no new extraction). Phase 8.1 adds the `BusinessLead` data model (`app/leads/`, `docs/leads.md`): a validated, serialisable representation of a business built from existing parsed values; the crawler does not create or store leads yet. Phase 8.2 adds deterministic business identity extraction (name, website, domain, description) from parsed pages. Phase 8.3 adds deterministic aggregation of the extracted values of one or more pages of a site into a single lead (`app/leads/aggregate.py`). Phase 8.4 persists leads (`leads` table, `LeadRepository`, one lead per domain). Phase 8.5 connects the pipeline end to end: `LeadCollector` (`app/leads/pipeline.py`), passed to the crawler as `page_observer`, creates and updates the domain's lead page by page, in the same transaction step as the page row (`docs/leads.md`). Phase 9.1 adds canonical website/domain identity (`app/urls/domain_identity.py`, `docs/urls.md`): one deterministic domain key per site, used by all Lead records. Phase 9.2 adds the deterministic, serializable `LeadIdentity` (`app/leads/lead_identity.py`, `docs/leads.md`), derived from that domain and equal to the stored domain name; no merging yet.

## Requirements
- Python 3.12+

## Setup
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env    # optional
```

## Run
```bash
python -m app.cli serve              # uses APP_HOST / APP_PORT
# or
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
```

## CLI
```bash
python -m app.cli --help
python -m app.cli version
python -m app.cli config
python -m app.cli serve --port 8001
python -m app.cli db upgrade | downgrade | current
```

## Test
```bash
pytest
```
Tests run fully offline.

## Layout
```
app/
  main.py        FastAPI app factory
  api/           HTTP routers (health)
  core/          config and logging
  cli/           Typer CLI
  db/            base, session/engine, UnitOfWork, migration helpers
  repositories/  data-access layer (one repository per model)
  models/        SQLAlchemy models (10 tables) and enums
  urls/          URL normalisation, canonical domain identity, scope, SSRF, trap and duplicate policy (no I/O)
  discovery/     robots.txt + sitemap discovery (injectable fetcher, safe XML parser)
  crawler/       async HTTP client (httpx), link extraction, BFS crawler, HTML parsing (Phase 6.1-6.2, 7.1)
  leads/         lead model, identity, aggregation, and the crawler lead hook (Phase 8.1-8.3, 8.5)
tests/           pytest suite
docs/            documentation
alembic/         Alembic environment and migrations
```

## Database
Set `APP_DATABASE_URL` (default: local SQLite file), then apply migrations:
```bash
python -m app.cli db upgrade     # create/upgrade the schema
python -m app.cli db current     # verify (exit 1 if not at head)
```
Use the data layer through a unit of work (commit on success, rollback on error):
```python
from app.db.uow import UnitOfWork

with UnitOfWork() as uow:
    domain, _ = uow.domains.get_or_create("example.com")
    uow.crawls.create(domain.id)
```
In FastAPI routes use `uow: UnitOfWork = Depends(get_uow)`. Details: `docs/database.md`, `alembic/README.md`.

## URL policy
```python
from app.core.config import get_settings
from app.urls import UrlEvaluator, UrlGate, UrlPolicy

gate = UrlGate(UrlEvaluator(UrlPolicy.from_settings(get_settings()), seed_url="example.com"))
gate.admit("/team/?utm_source=x", base="https://example.com/")   # -> allowed, normalised URL
```
All thresholds are `APP_URL_*` settings. Details: `docs/urls.md`.

## Sitemap discovery (Phase 5, complete)
```python
from app.core.config import get_settings
from app.discovery import build_discovery

result = build_discovery(get_settings(), "example.com").discover("example.com")
result.urls   # page URLs that passed the URL policy
```
Target -> robots.txt -> Sitemap declarations -> `/sitemap.xml` fallback -> sitemap
(`urlset` -> page URLs; `sitemapindex` -> child sitemaps -> page URLs).
Nested indexes are followed up to `APP_DISCOVERY_MAX_DEPTH` (default 5) with cycle detection. Limits: `APP_DISCOVERY_MAX_DEPTH`, `APP_DISCOVERY_MAX_SITEMAPS`, `APP_DISCOVERY_MAX_URLS`, `APP_DISCOVERY_MAX_BYTES`. Time budgets and gzip are not supported. Discovery only lists URLs; it never fetches the pages. Details: `docs/discovery.md`.

## Configuration
See `.env.example` and `docs/configuration.md`.

## Lead management (Phase 11.5)

`leadfinder leads list|search|export` support filters, `--sort-by`, `--desc/--asc`, `--page`, `--page-size`; `export` writes JSON or CSV of the selected page (UTF-8, read-only, offline). See `docs/leads.md`.
