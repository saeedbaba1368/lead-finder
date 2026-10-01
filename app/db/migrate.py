"""Programmatic Alembic helpers (used by the CLI and the tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool

from app.core.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = PROJECT_ROOT / "alembic"


def make_alembic_config(database_url: str | None = None) -> Config:
    """Build an Alembic config that does not depend on the current working directory."""
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.attributes["database_url"] = database_url or get_settings().database_url
    return cfg


def upgrade(database_url: str | None = None, revision: str = "head") -> None:
    command.upgrade(make_alembic_config(database_url), revision)


def downgrade(database_url: str | None = None, revision: str = "-1") -> None:
    command.downgrade(make_alembic_config(database_url), revision)


def current_revision(database_url: str | None = None) -> str | None:
    """Return the revision the database is at, or None if unmigrated."""
    url = make_alembic_config(database_url).attributes["database_url"]
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(make_alembic_config()).get_current_head()
