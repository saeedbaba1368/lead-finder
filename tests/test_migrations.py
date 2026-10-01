import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from typer.testing import CliRunner

import app.models  # noqa: F401
from app.cli.main import app as cli_app
from app.core.config import get_settings
from app.db import migrate
from app.db.base import Base

MODEL_TABLES = set(Base.metadata.tables)


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'mig.db'}"


def table_names(url):
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_single_head_and_linear_history():
    script = ScriptDirectory.from_config(migrate.make_alembic_config("sqlite://"))
    assert len(script.get_heads()) == 1
    assert script.get_current_head() == "0006"


def test_upgrade_creates_every_model_table(db_url):
    assert migrate.current_revision(db_url) is None
    migrate.upgrade(db_url)
    assert table_names(db_url) == MODEL_TABLES | {"alembic_version"}
    assert migrate.current_revision(db_url) == migrate.head_revision()


def test_migrated_schema_matches_models_no_drift(db_url):
    """If this fails, a model changed without a new migration (or vice versa)."""
    migrate.upgrade(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_to_base_then_upgrade_again(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url, "base")
    assert table_names(db_url) == {"alembic_version"}
    assert migrate.current_revision(db_url) is None
    migrate.upgrade(db_url)
    assert table_names(db_url) == MODEL_TABLES | {"alembic_version"}


def test_upgrade_is_idempotent(db_url):
    migrate.upgrade(db_url)
    migrate.upgrade(db_url)
    assert migrate.current_revision(db_url) == "0006"


def test_migrated_schema_enforces_constraints_and_cascades(db_url):
    migrate.upgrade(db_url)
    conn = sqlite3.connect(db_url.removeprefix("sqlite:///"))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("insert into domains (name) values ('a.com')")
    conn.execute("insert into crawls (domain_id) values (1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into domains (name) values ('A.COM')")  # lowercase check
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("update crawls set status='bogus'")  # single enum CHECK
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into crawls (domain_id, max_pages) values (1, 0)")
    conn.execute("delete from domains")
    assert conn.execute("select count(*) from crawls").fetchone()[0] == 0  # FK cascade
    conn.close()


def test_each_enum_column_has_exactly_one_check(db_url):
    migrate.upgrade(db_url)
    conn = sqlite3.connect(db_url.removeprefix("sqlite:///"))
    sql = conn.execute("select sql from sqlite_master where name='crawls'").fetchone()[0]
    conn.close()
    assert sql.count("status IN") == 1


def test_migration_sql_is_portable_to_postgresql(tmp_path, capsys):
    """Offline mode renders PostgreSQL DDL without a server or driver."""
    cfg = migrate.make_alembic_config("postgresql+psycopg://u:p@h/d")
    command.upgrade(cfg, "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TABLE domains" in out
    assert "DEFAULT true" in out and "DEFAULT now()" in out
    assert "DEFAULT 1 " not in out and "CURRENT_TIMESTAMP" not in out


def test_env_uses_app_database_url_when_not_overridden(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'env.db'}"
    monkeypatch.setenv("APP_DATABASE_URL", url)
    get_settings.cache_clear()
    migrate.upgrade()  # no explicit url -> settings
    assert "domains" in table_names(url)


def test_cli_db_commands(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("APP_DATABASE_URL", url)
    get_settings.cache_clear()
    runner = CliRunner()

    assert runner.invoke(cli_app, ["db", "current"]).exit_code == 1  # unmigrated
    up = runner.invoke(cli_app, ["db", "upgrade"])
    assert up.exit_code == 0 and "0006" in up.output
    cur = runner.invoke(cli_app, ["db", "current"])
    assert cur.exit_code == 0 and "current: 0006" in cur.output
    down = runner.invoke(cli_app, ["db", "downgrade", "base"])
    assert down.exit_code == 0 and "None" in down.output


def test_raw_connection_can_be_passed_to_env():
    """Tests/tools can run migrations on an existing connection via config.attributes."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        cfg = migrate.make_alembic_config("sqlite://")
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        assert {"domains", "emails"} <= set(inspect(conn).get_table_names())
        assert conn.execute(text("select version_num from alembic_version")).scalar() == "0006"
