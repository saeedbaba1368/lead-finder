import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_db_engine, get_engine


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_LOG_JSON", "false")
    monkeypatch.setenv("APP_DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def engine():
    import app.models  # noqa: F401  (register tables)

    eng = create_db_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    """PROJECT_RULES #3: tests never touch the network. Any DNS lookup or connect fails loudly."""
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted during a test")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
