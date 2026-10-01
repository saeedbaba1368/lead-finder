import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db import migrate
from app.db.session import create_db_engine, get_engine, session_scope
from app.db.uow import UnitOfWork, get_uow
from app.models import CrawlStatus, Domain


@pytest.fixture
def factory(tmp_path):
    """File-backed migrated DB so separate sessions see each other's committed data."""
    url = f"sqlite:///{tmp_path / 'uow.db'}"
    migrate.upgrade(url)
    engine = create_db_engine(url)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def names(factory):
    with factory() as s:
        return [d.name for d in s.query(Domain).order_by(Domain.id)]


def test_uow_commits_on_success(factory):
    with UnitOfWork(factory) as uow:
        uow.domains.get_or_create("a.com")
    assert names(factory) == ["a.com"]


def test_uow_rolls_back_on_exception(factory):
    with pytest.raises(RuntimeError):
        with UnitOfWork(factory) as uow:
            uow.domains.get_or_create("a.com")
            raise RuntimeError("boom")
    assert names(factory) == []


def test_uow_rolls_back_on_integrity_error_and_all_work_is_atomic(factory):
    with pytest.raises(Exception):
        with UnitOfWork(factory) as uow:
            uow.domains.get_or_create("ok.com")
            uow.domains.add(Domain(name="dup.com"))
            uow.domains.add(Domain(name="dup.com"))
    assert names(factory) == []


def test_uow_explicit_commit_and_rollback(factory):
    with UnitOfWork(factory) as uow:
        uow.domains.get_or_create("keep.com")
        uow.commit()
        uow.domains.get_or_create("drop.com")
        uow.rollback()
    assert names(factory) == ["keep.com"]


def test_savepoint_undoes_only_inner_work(factory):
    with UnitOfWork(factory) as uow:
        uow.domains.get_or_create("outer.com")
        with pytest.raises(ValueError):
            with uow.savepoint():
                uow.domains.get_or_create("inner.com")
                raise ValueError("undo inner")
        uow.domains.get_or_create("after.com")
    assert names(factory) == ["outer.com", "after.com"]


def test_uow_repositories_share_one_session(factory):
    with UnitOfWork(factory) as uow:
        d, _ = uow.domains.get_or_create("a.com")
        c = uow.crawls.create(d.id)
        uow.crawls.set_status(c, CrawlStatus.RUNNING)
        assert uow.session is uow.emails.session is uow.api_keys.session
    with UnitOfWork(factory) as uow:
        assert uow.crawls.list_by_status(CrawlStatus.RUNNING)[0].domain.name == "a.com"


def test_uow_with_external_session_does_not_commit_or_close(factory):
    with factory() as s:
        with UnitOfWork(session=s) as uow:
            uow.domains.get_or_create("a.com")
        assert s.in_transaction()  # still open, uncommitted
        s.rollback()
    assert names(factory) == []


def test_session_scope_commit_and_rollback(factory):
    with session_scope(factory) as s:
        s.add(Domain(name="a.com"))
    with pytest.raises(RuntimeError):
        with session_scope(factory) as s:
            s.add(Domain(name="b.com"))
            raise RuntimeError
    assert names(factory) == ["a.com"]


def test_fastapi_dependency_commits_and_rolls_back(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'api.db'}"
    migrate.upgrade(url)
    monkeypatch.setenv("APP_DATABASE_URL", url)
    get_settings.cache_clear()
    get_engine.cache_clear()

    app = FastAPI()

    @app.post("/domains/{name}")
    def add(name: str, uow: UnitOfWork = Depends(get_uow)):
        uow.domains.get_or_create(name)
        return {"ok": True}

    @app.post("/fail/{name}")
    def fail(name: str, uow: UnitOfWork = Depends(get_uow)):
        uow.domains.get_or_create(name)
        raise RuntimeError("handler blew up")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/domains/kept.com").status_code == 200
    assert client.post("/fail/lost.com").status_code == 500
    with Session(create_engine(url)) as s:
        assert [d.name for d in s.query(Domain)] == ["kept.com"]
    get_engine().dispose()
    get_engine.cache_clear()
