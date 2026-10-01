# Database model layer (Phase 2)

SQLAlchemy 2 declarative models in `app/models/`; infrastructure in `app/db/`.
Works on SQLite (default, used by tests) and PostgreSQL (DDL compile-checked in tests).

## Conventions
- Integer surrogate PKs (`BIGINT`, `INTEGER` on SQLite). Every table has timezone-aware `created_at` / `updated_at`.
- Deterministic constraint names via `NAMING_CONVENTION` (`app/db/base.py`) so Alembic migrations stay stable.
- Enums are `StrEnum`s stored as `VARCHAR` with a named CHECK constraint (portable, no native enum types).
- Domain names and email addresses are normalised to lowercase by validators and enforced by CHECK constraints.
- SQLite connections created by `create_db_engine` run `PRAGMA foreign_keys=ON`, so cascades behave the same as on PostgreSQL.
- Configure with `APP_DATABASE_URL` (default `sqlite:///./leadfinder.db`) and `APP_DB_ECHO`. `python -m app.cli config` masks passwords.

## Tables and relationships
```
api_keys ──< crawls >── domains ──< emails ──< email_evidence >── pages >── crawls
                          │            │  └──< email_verifications
                          │            └──< people (email_id, optional)
                          ├──< people ──< person_evidence >── pages
                          └──< email_patterns
```

| Table | Purpose | Key constraints / indexes |
|---|---|---|
| `domains` | Domain being researched | unique lowercase `name` |
| `crawls` | A crawl run per domain | status enum; `max_pages>0`, `max_depth>=0`, `pages_crawled>=0`, `finished_at>=started_at`; resume state (migration `0003`): `max_crawl_time`, `request_delay`, `current_depth`, `elapsed_seconds`, `stop_reason`, `last_checkpoint_at`; idx `(domain_id,status)`, `(status,created_at)`, `api_key_id` |
| `pages` | URL within a crawl (no bodies stored) | unique `(crawl_id,url_hash)`; `depth>=0`; HTTP status 100-599; idx `(crawl_id,status)`, `content_hash`, `dedupe_key`; `parent_url` (BFS queue state, migration `0003`; PENDING = queued, SKIPPED = left out by `max_pages`, see `app/crawler/resume.py`); fetch metadata (migration `0002`): `final_url`, `charset`, `response_size`, `duration_seconds`, `redirect_count`/`redirect_chain`, `outcome`, `dedupe_key` |
| `emails` | Address found for a domain | unique `(domain_id,address)`; lowercase + format checks; idx `address` |
| `email_evidence` | Where an email was seen | unique `(email_id,page_id,source_type)`; confidence 0-1; occurrences>=1; idx `page_id` |
| `people` | Person tied to a domain, optional email | confidence 0-1; idx `(domain_id,last_name,first_name)`, `email_id` |
| `person_evidence` | Where a person was seen | unique `(person_id,page_id,source_type)`; confidence 0-1; idx `page_id` |
| `email_verifications` | Verification attempt history | SMTP code 100-599; idx `(email_id,checked_at)`, `status` |
| `email_patterns` | Naming pattern per domain | unique `(domain_id,pattern)`; sample_count>=0; confidence 0-1 |
| `api_keys` | API credentials (hash only) | unique `key_prefix`, unique `key_hash` |

## Delete behaviour
- Delete a **domain** → its crawls, pages, emails, evidence, verifications, people, and patterns are deleted (`ON DELETE CASCADE`).
- Delete a **crawl** → its pages and the evidence pointing at them are deleted; emails and people are kept.
- Delete an **email** → its evidence and verifications are deleted; linked people are kept (`email_id` set to NULL).
- Delete an **API key** → crawls are kept (`api_key_id` set to NULL).

## Migrations (Alembic)
- Config: `alembic.ini`, `alembic/env.py`; programmatic API in `app/db/migrate.py`; CLI `python -m app.cli db upgrade|downgrade|current`.
- `0001_initial_schema` creates all ten tables, indexes and constraints. It was generated with `--autogenerate` and then hand-corrected:
  - removed duplicate enum CHECK constraints (autogenerate emitted two per enum column);
  - replaced SQLite-flavoured defaults with dialect-neutral ones (`sa.func.now()`, `sa.true()`, `sa.false()`), so the same migration works on PostgreSQL (`DEFAULT true`, `now()`), verified by offline SQL generation in tests.
- `0002_page_fetch_metadata` adds the `pages` fetch-metadata columns, their CHECK constraints and `ix_pages_dedupe_key` (Phase 6.3.2.2.1).
- `0003_crawl_resume_state` adds the `crawls` resume-state columns and constraints and `pages.parent_url` (Phase 6.3.2.2.2; data model only, no resume logic yet).
- `0004_crawl_stop_reason_cancelled` adds `cancelled` to the `crawls.stop_reason` CHECK (Phase 6.4). Lifecycle (created / running / stopping / completed / stopped / failed) is derived from `status` + `stop_reason`, see `app/crawler/lifecycle.py`.
- A drift test compares the migrated schema with `Base.metadata`; `alembic check` reports no pending changes.
- The app does **not** auto-migrate on startup; run `db upgrade` as a deploy step.

## Sessions and transactions
- `app/db/session.py`: lazy engine, `get_session_factory`, `session_scope()` (commit on success, rollback on error, always close), `get_session` FastAPI dependency (rolls back on error, never commits).
- `app/db/uow.py`: `UnitOfWork` bundles one session with every repository: commit on clean exit, rollback on any exception, `savepoint()` for partial rollback, `get_uow` FastAPI dependency.
- **SQLite transaction handling:** `create_db_engine` applies the documented pysqlite workaround (driver no longer auto-BEGINs; the engine emits BEGIN). Without it, SAVEPOINT-before-first-write followed by RELEASE commits the outer transaction and later rollbacks silently fail to undo work. A regression test covers this.
- Repositories flush but never commit; the caller or unit of work owns the transaction.

## Repositories (`app/repositories/`)
All extend `BaseRepository`: `get`, `require` (raises `NotFoundError`), `list`, `count`, `add`, `update` (whitelisted column fields only), `delete`, `delete_by_id`. Constraint violations surface as `sqlalchemy.exc.IntegrityError`.

| Repository | Additional operations |
|---|---|
| `DomainRepository` | `get_by_name`, `get_or_create` (savepoint, race-safe), `search`, `get_with_relations`, `mark_crawled` |
| `CrawlRepository` | `create`, `get_with_pages`, `list_for_domain`, `list_by_status`, `count_by_status`, `set_status` (validated lifecycle; sets timestamps; COMPLETED stamps the domain), `increment_pages_crawled` |
| `PageRepository` | `create` (computes `url_hash`), `get_by_url`, `get_or_create`, `list_for_crawl`, `count_for_crawl`, `mark_fetched`, `mark_failed` (both accept the optional fetch metadata) |
| `EmailRepository` | `get_by_address`, `find_by_address`, `get_or_create`, `get_full`, `list_for_domain` (role/search filters), `touch_seen`, `add_evidence` (upsert: bumps `occurrences`, keeps max confidence), `list_evidence` |
| `PersonRepository` | `create`, `get_full`, `find_by_name`, `list_for_domain`, `link_email` (same-domain enforced), `add_evidence` (upsert), `list_evidence` |
| `EmailVerificationRepository` | `record`, `list_for_email` (newest first), `latest_for_email` |
| `EmailPatternRepository` | `get_for_domain`, `upsert`, `list_for_domain` (most confident first) |
| `ApiKeyRepository` | `create_new` (returns plaintext once; stores hash), `get_by_prefix`, `get_by_secret`, `get_usable_by_secret`, `list_keys`, `revoke`, `touch_last_used` |

Crawl lifecycle: `pending -> running | failed | cancelled`, `running -> completed | failed | cancelled`; terminal states are final (`InvalidTransitionError`).

### Relationship loading
Default relationship loading stays lazy. Explicit eager variants use `selectinload` (no join explosion, no N+1): `get_with_relations`, `get_with_pages`, `EmailRepository.get_full`, `PersonRepository.get_full`, and `with_evidence` / `with_email` flags on list methods. Eager reads use `populate_existing` so they also fill instances already in the session. Tests assert exact query counts.

## Not included yet
Crawler, extraction, verification logic, API endpoints for the data (the layer is ready: use `Depends(get_uow)`).

## Leads (Phase 8.4)
Migration `0005_leads` adds the `leads` table (one row per domain, unique `domain_id`; `crawl_id` `ON DELETE SET NULL`). Delete a **domain** -> its lead is deleted; delete a **crawl** -> the lead is kept. Repository: `LeadRepository` (`save`, `merge`, `load`, `load_all`, `get_by_domain`); see `docs/leads.md`.

Migration `0006_lead_website_metadata` adds the nullable JSON column `leads.website_metadata` (Phase 12.1; `WebsiteMetadata.to_dict()`). `LeadRepository` gained `merge_metadata` and `load_metadata`.
