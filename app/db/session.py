"""Engine and session helpers. Engines are created lazily; nothing connects at import.

Transaction model: sessions never autocommit. Use ``session_scope`` (or the
``UnitOfWork`` in ``app.db.uow``) to get commit-on-success / rollback-on-error.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_db_engine(url: str | None = None, *, echo: bool | None = None) -> Engine:
    """Create an engine. SQLite connections get foreign-key enforcement turned on."""
    settings = get_settings()
    engine = create_engine(
        url or settings.database_url,
        echo=settings.db_echo if echo is None else echo,
    )
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_connection, _record) -> None:  # noqa: ANN001
            # Enforce foreign keys (cascades / SET NULL behave like PostgreSQL).
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            # pysqlite workaround (SQLAlchemy docs, "Serializable isolation / Savepoints /
            # Transactional DDL"): stop the driver from deciding when to BEGIN, otherwise a
            # SAVEPOINT issued before the first DML starts its own transaction and RELEASE
            # commits it, so a later rollback would not undo the work.
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn) -> None:  # noqa: ANN001
            conn.exec_driver_sql("BEGIN")

    return engine


@lru_cache
def get_engine() -> Engine:
    return create_db_engine()


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Commit if the block succeeds, roll back if it raises; always close."""
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session, rolls back on error, always closes.

    It does not commit; the caller decides. Prefer ``get_uow`` for write endpoints.
    """
    session = get_session_factory()()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
