import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_defaults_and_extra_env_ignored(monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_VAR", "x")
    s = get_settings()
    assert s.database_url == "sqlite+aiosqlite:///:memory:"
    assert s.paypal_base_url == "https://api-m.sandbox.paypal.com"
    assert s.demo_enabled is False
    assert s.demo_replay_mode is False


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
