from sqlalchemy import text

from app.cli.main import app as cli_app
from app.db.session import create_db_engine, get_session
from typer.testing import CliRunner


def test_sqlite_engine_enables_foreign_keys():
    engine = create_db_engine("sqlite://")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_get_session_yields_working_session():
    gen = get_session()
    session = next(gen)
    assert session.execute(text("select 1")).scalar() == 1
    gen.close()


def test_cli_config_masks_database_password(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://user:s3cret@db/app")
    get_settings.cache_clear()
    result = CliRunner().invoke(cli_app, ["config"])
    assert result.exit_code == 0
    assert "s3cret" not in result.output and "user:***@db" in result.output


def test_sqlite_savepoint_before_first_write_does_not_commit_outer_work(tmp_path):
    """Regression: with stock pysqlite, SAVEPOINT-before-DML + RELEASE commits the outer txn."""
    from sqlalchemy.orm import Session

    from app.db import migrate
    from app.models import Domain

    url = f"sqlite:///{tmp_path / 'sp.db'}"
    migrate.upgrade(url)
    engine = create_db_engine(url)
    with Session(engine) as s:
        with s.begin_nested():  # first statement in the transaction is a SAVEPOINT
            s.add(Domain(name="a.com"))
            s.flush()
        s.rollback()  # must undo the released savepoint too
    with Session(engine) as s:
        assert s.query(Domain).count() == 0
    engine.dispose()
