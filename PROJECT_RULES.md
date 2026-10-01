# PROJECT_RULES.md

Rules that apply to every phase of this project.

1. **Phase discipline.** Implement only what the current phase file specifies. Do not build ahead. Stop at the ZIP checkpoint.
2. **Out of scope until explicitly scheduled:** crawler (scheduled fetching so far: Phase 5.x sitemap discovery and the Phase 6.1 async HTTP client and the Phase 6.2 BFS internal crawler; crawl-time limits, rate limiting, persistence, resume and lifecycle through Phase 6.4, HTML content parsing in Phase 7.1, and lead creation from crawled pages in Phase 8.5; Playwright and contact/advanced extraction are not yet scheduled), email extraction, people detection, email finder, verification, frontend, background jobs.
3. **Offline first.** The project must start and `pytest` must pass with no network access. Tests must never make real network calls.
4. **Python 3.12.** Use type hints; keep modules small and single-purpose.
5. **Configuration via environment.** All settings live in `app/core/config.py`, use the `APP_` prefix, and are documented in `.env.example`. Never commit secrets or a real `.env`.
6. **Structured logging.** Use `app.core.logging.get_logger`; log events with `extra=` fields rather than string-formatted data. No `print` in application code (CLI output via `typer.echo` is fine).
7. **Tests.** Every feature ships with tests. `pytest` must be green before a checkpoint.
8. **Documentation upkeep.** At each checkpoint update `README.md`, `CURRENT_STATE.md`, `CHANGELOG.md`, and `PHASE_REPORT.md`.
9. **Clean deliverables.** ZIP checkpoints (`vN.zip`) exclude caches, virtualenvs, `.env`, and build artefacts.
10. **Honest reporting.** Reports state what was actually run and its result; known gaps are listed, not hidden.
11. **Migrations.** Every model change ships with an Alembic migration that is reviewed by hand (never trust raw autogenerate). The drift test must stay green.
12. **Transactions.** Repositories flush, they never commit; commit/rollback belongs to `UnitOfWork` / `session_scope`.
