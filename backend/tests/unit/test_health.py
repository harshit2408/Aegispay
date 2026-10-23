import httpx

from app.config import get_settings
from app.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_ok():
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": True}


async def test_health_503_on_broken_db_without_leaking_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////nonexistent-aegis-dir/x/y.db")
    get_settings.cache_clear()
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "db": False}
