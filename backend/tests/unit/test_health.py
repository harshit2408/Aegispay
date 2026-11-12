import httpx

from app.config import get_settings
from app.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_ok():
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    # SQLite has no settlement report: reachable DB is HTTP 200, everything else reads unknown.
    assert (body["status"], body["db"]) == ("degraded", True)
    assert (body["demo_enabled"], body["replay_mode"]) == (False, False)
    assert body["settlement_alive"] is False
    assert set(body["checks"].values()) == {None}


async def test_health_reports_replay_flag_when_replay_mode_on(monkeypatch):
    # R24 / §16 Honesty: the UI REPLAY banner reads this flag.
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_REPLAY_MODE", "true")
    get_settings.cache_clear()
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 200
    assert (r.json()["demo_enabled"], r.json()["replay_mode"]) == (True, True)


async def test_health_503_on_broken_db_without_leaking_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////nonexistent-aegis-dir/x/y.db")
    get_settings.cache_clear()
    async with _client() as c:
        r = await c.get("/api/health")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "db": False}
