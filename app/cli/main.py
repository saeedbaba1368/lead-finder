"""Basic CLI skeleton. Future phases add subcommands here."""

import json

import typer
from sqlalchemy.engine import make_url

from app import __version__
from app.core.config import get_settings

app = typer.Typer(help="leadfinder command-line interface.", no_args_is_help=True)
db_app = typer.Typer(help="Database migration commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")
crawl_app = typer.Typer(help="Inspect persisted crawls.", no_args_is_help=True)
app.add_typer(crawl_app, name="crawl")
leads_app = typer.Typer(help="Inspect stored leads.", no_args_is_help=True)
app.add_typer(leads_app, name="leads")


@app.command()
def version() -> None:
    """Print the application version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the effective configuration as JSON."""
    data = get_settings().model_dump()
    data["database_url"] = make_url(data["database_url"]).render_as_string(hide_password=True)
    typer.echo(json.dumps(data, indent=2))


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Override APP_HOST."),
    port: int | None = typer.Option(None, help="Override APP_PORT."),
    reload: bool = typer.Option(False, help="Enable auto-reload (development)."),
) -> None:
    """Run the API server with uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        log_config=None,
    )


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head", help="Target revision.")) -> None:
    """Apply migrations (default: up to head)."""
    from app.db import migrate

    migrate.upgrade(revision=revision)
    typer.echo(f"database at: {migrate.current_revision()}")


@db_app.command("downgrade")
def db_downgrade(revision: str = typer.Argument("-1", help="Target revision ('-1' = one step, 'base' = empty).")) -> None:
    """Roll migrations back (default: one step)."""
    from app.db import migrate

    migrate.downgrade(revision=revision)
    typer.echo(f"database at: {migrate.current_revision()}")


@db_app.command("current")
def db_current() -> None:
    """Show the current and head revisions; exit 1 if the database is not at head."""
    from app.db import migrate

    current, head = migrate.current_revision(), migrate.head_revision()
    typer.echo(f"current: {current}")
    typer.echo(f"head:    {head}")
    if current != head:
        raise typer.Exit(code=1)


@crawl_app.command("state")
def crawl_state(
    crawl_id: int = typer.Argument(..., help="Crawl id."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of text."),
) -> None:
    """Show the persisted state of a crawl (read-only; exit 1 if the crawl does not exist)."""
    from app.crawler.state import inspect_crawl_state
    from app.db.session import session_scope

    with session_scope() as session:
        report = inspect_crawl_state(session, crawl_id)
    if report is None:
        typer.echo(f"crawl {crawl_id} not found", err=True)
        raise typer.Exit(code=1)
    data = report.to_dict()
    if as_json:
        typer.echo(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            typer.echo(f"{key + ':':20} {value}")


def _build_filter(domain, language, country, city, page_type, has_email, has_phone, has_social_profile):
    """The 10.3 `LeadFilter` of the filter options; an invalid value prints `invalid filter: ...` and exits 2."""
    from app.leads.filters import LeadFilter, LeadFilterError

    try:  # validated before the database is touched
        return LeadFilter(
            domain=domain or None, language=language or None, country=country or None, city=city or None,
            page_type=page_type or None, has_email=has_email, has_phone=has_phone, has_social_profile=has_social_profile,
        )
    except LeadFilterError as exc:
        typer.echo(f"invalid filter: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _check_output_controls(sort_by, descending, page, page_size) -> None:
    """Validate sort/page options with the 10.4 rules before the database is opened; invalid -> `invalid paging: ...`, exit 2."""
    from app.leads.errors import LeadDataError
    from app.leads.query import LeadQuery

    try:
        LeadQuery(sort_by=sort_by, descending=descending, page=page, page_size=page_size)
    except LeadDataError as exc:
        typer.echo(f"invalid paging: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _print_leads(flt, empty_message: str, sort_by: str = "domain", descending: bool = False, page=None, page_size=None) -> None:
    """Read the stored leads (all, or those matching `flt`), sort and page them (10.4), print them. Read-only."""
    from sqlalchemy import inspect

    from app.db.session import session_scope
    from app.leads.listing import format_lead_listing, list_leads_page
    from app.repositories import LeadRepository

    _check_output_controls(sort_by, descending, page, page_size)
    with session_scope() as session:
        if not inspect(session.get_bind()).has_table("leads"):
            typer.echo("the database has no leads table; run `db upgrade` first", err=True)
            raise typer.Exit(code=1)
        listing = list_leads_page(LeadRepository(session), flt, sort_by, descending, page, page_size)
    if listing.paged and listing.total > 0 and not listing.rows:
        message = listing.empty_message
    else:
        message = empty_message
    typer.echo(format_lead_listing(list(listing.rows), message))
    if listing.summary:
        typer.echo(listing.summary, err=True)


SORT_BY_OPTION = typer.Option("domain", "--sort-by", help="Sort field: business_name, domain, first_seen or last_seen.")
DESC_OPTION = typer.Option(False, "--desc/--asc", help="Descending / ascending order (default ascending).")
PAGE_OPTION = typer.Option(None, "--page", help="Page number, starting at 1 (turns paging on).")
PAGE_SIZE_OPTION = typer.Option(None, "--page-size", help="Leads per page, 1-1000 (turns paging on; default 50).")


@leads_app.command("list")
def leads_list(
    sort_by: str = SORT_BY_OPTION, desc: bool = DESC_OPTION, page: int | None = PAGE_OPTION,
    page_size: int | None = PAGE_SIZE_OPTION,
) -> None:
    """List stored leads (read-only; no network; exit 1 if the database has no leads table).

    Default: every lead in domain order. --sort-by/--desc/--asc order them; --page/--page-size show one page (the page
    summary goes to standard error). Ties are broken by domain, so the order is deterministic. Exit 2 on invalid values.
    """
    from app.leads.listing import EMPTY_MESSAGE

    _print_leads(None, EMPTY_MESSAGE, sort_by, desc, page, page_size)


@leads_app.command("search")
def leads_search(
    domain: list[str] | None = typer.Option(None, "--domain", help="Domain or URL (repeat for alternatives)."),
    language: list[str] | None = typer.Option(None, "--language", help="Language code, e.g. en, fa (repeatable)."),
    country: list[str] | None = typer.Option(None, "--country", help="Address country (repeatable)."),
    city: list[str] | None = typer.Option(None, "--city", help="Address city (repeatable)."),
    page_type: list[str] | None = typer.Option(None, "--page-type", help="Page type, e.g. homepage, contact (repeatable)."),
    has_email: bool | None = typer.Option(None, "--has-email/--no-email", help="Only leads with / without an email."),
    has_phone: bool | None = typer.Option(None, "--has-phone/--no-phone", help="Only leads with / without a phone number."),
    has_social_profile: bool | None = typer.Option(
        None, "--has-social-profile/--no-social-profile", help="Only leads with / without a social profile."
    ),
    sort_by: str = SORT_BY_OPTION, desc: bool = DESC_OPTION, page: int | None = PAGE_OPTION,
    page_size: int | None = PAGE_SIZE_OPTION,
) -> None:
    """Search stored leads with the Phase 10.3 filters (read-only; no network).

    Filters combine with AND; repeating one option gives alternatives (OR). No filter lists every lead. Sorting and
    paging (--sort-by, --desc/--asc, --page, --page-size) apply after filtering, as in `leads list`. Exit 2 on an
    invalid filter or paging value, exit 1 if the database has no leads table.
    """
    from app.leads.listing import EMPTY_MESSAGE

    flt = _build_filter(domain, language, country, city, page_type, has_email, has_phone, has_social_profile)
    _print_leads(flt, EMPTY_MESSAGE if flt.is_empty else "No leads match the filters.", sort_by, desc, page, page_size)


@leads_app.command("export")
def leads_export(
    export_format: str = typer.Option("json", "--format", "-f", help="Export format: json or csv."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to this file instead of standard output."),
    excel_bom: bool = typer.Option(False, "--excel-bom", help="CSV only: add a UTF-8 BOM so Excel reads Persian text."),
    domain: list[str] | None = typer.Option(None, "--domain", help="Domain or URL (repeat for alternatives)."),
    language: list[str] | None = typer.Option(None, "--language", help="Language code, e.g. en, fa (repeatable)."),
    country: list[str] | None = typer.Option(None, "--country", help="Address country (repeatable)."),
    city: list[str] | None = typer.Option(None, "--city", help="Address city (repeatable)."),
    page_type: list[str] | None = typer.Option(None, "--page-type", help="Page type, e.g. homepage, contact (repeatable)."),
    has_email: bool | None = typer.Option(None, "--has-email/--no-email", help="Only leads with / without an email."),
    has_phone: bool | None = typer.Option(None, "--has-phone/--no-phone", help="Only leads with / without a phone number."),
    has_social_profile: bool | None = typer.Option(
        None, "--has-social-profile/--no-social-profile", help="Only leads with / without a social profile."
    ),
    sort_by: str = SORT_BY_OPTION, desc: bool = DESC_OPTION, page: int | None = PAGE_OPTION,
    page_size: int | None = PAGE_SIZE_OPTION,
) -> None:
    """Export stored leads as JSON or CSV (Phase 10 exports; read-only; no network).

    The filters are those of `leads search`; no filter exports every lead. Sorting and paging (--sort-by, --desc/--asc,
    --page, --page-size) apply after filtering, as in `leads list`, and the file holds exactly the selected leads in
    that order (default: every lead in domain order). Output is UTF-8 and identical for identical data. No match, or a
    page beyond the last, gives a valid empty export. Exit 2 on an invalid format, filter or paging value, exit 1 if
    the database has no leads table or the file cannot be written.
    """
    import click
    from sqlalchemy import inspect

    from app.db.session import session_scope
    from app.leads.export_command import LeadExportError, render_export, select_lead_page, validate_export, write_export
    from app.repositories import LeadRepository

    try:  # everything is validated before the database is opened or a file is touched
        fmt = validate_export(export_format, excel_bom)
    except LeadExportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    flt = _build_filter(domain, language, country, city, page_type, has_email, has_phone, has_social_profile)
    _check_output_controls(sort_by, desc, page, page_size)

    with session_scope() as session:
        if not inspect(session.get_bind()).has_table("leads"):
            typer.echo("the database has no leads table; run `db upgrade` first", err=True)
            raise typer.Exit(code=1)
        leads = select_lead_page(LeadRepository(session), flt, sort_by, desc, page, page_size).leads
    if output is None:
        stream = click.get_binary_stream("stdout")
        stream.write(render_export(leads, fmt, excel_bom=excel_bom))
        stream.flush()
        return
    try:
        write_export(output, leads, fmt, excel_bom=excel_bom)
    except OSError as exc:
        typer.echo(f"cannot write {output}: {exc.strerror or exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {len(leads)} lead(s) to {output}")


if __name__ == "__main__":
    app()
