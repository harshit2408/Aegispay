import pytest

from app.config import get_settings

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Each test sees a fresh Settings built from its own env."""
    monkeypatch.setenv("DATABASE_URL", SQLITE_URL)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
