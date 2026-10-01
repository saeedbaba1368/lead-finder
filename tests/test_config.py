import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings(_env_file=None)
    assert s.env == "development"
    assert s.port == 8000


def test_env_override(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9001")
    get_settings.cache_clear()
    assert get_settings().port == 9001


def test_invalid_port_rejected(monkeypatch):
    monkeypatch.setenv("APP_PORT", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
