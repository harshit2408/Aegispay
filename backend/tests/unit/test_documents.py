from datetime import UTC, datetime, timedelta
from time import perf_counter
from unittest.mock import AsyncMock

import httpx

from app.auth import COOKIE, csrf_token, token_hash
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models.auth import OperatorSession
from app.models.core import MandatePolicy
from app.schemas.decision import MandatePolicy as Policy
from tests.seed import seed_approved, seed_world


async def test_s4_duplicate_links_original_without_vendor_call(
    pg_sessionmaker, tmp_path, monkeypatch, record_property
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/unused")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-s4-test-session-key-123456")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    token = "synthetic-s4-session"
    now = datetime.now(UTC)
    async with pg_sessionmaker.begin() as session:
        await seed_world(session)
        await seed_approved(session)
        policy = await session.get(MandatePolicy, ("pol-1", 1))
        policy.compiled_json = Policy(
            policy_id="pol-1",
            version=1,
            daily_cap_cents=500000,
            max_single_invoice_cents=100000,
            vendor_daily_cap_cents=150000,
        ).model_dump(mode="json")
        session.add(
            OperatorSession(
                token_hash=token_hash(token),
                operator_id="op-ctl",
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, sessions)
    reader = AsyncMock()
    monkeypatch.setattr(app.state, "vendor_reader", reader, raising=False)
    monkeypatch.setattr(app.state, "dispatcher", object(), raising=False)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            cookies={COOKIE: token},
        ) as client:
            start = perf_counter()
            result = await client.post(
                "/api/documents",
                headers={"X-CSRF-Token": csrf_token(token)},
                data={"po_id": "PO-1042", "paypal_invoice_id": "INV2-ABC"},
                files={"file": ("invoice.pdf", b"%PDF-1.7", "application/pdf")},
            )
            elapsed = perf_counter() - start
            record_property("duplicate_latency_ms", round(elapsed * 1000, 2))
            assert result.status_code == 202, result.text
            assert elapsed < 1
            receipt = result.json()
            assert receipt["duplicate_of_execution_id"] == "e1"
            case = await client.get("/api/executions/" + receipt["execution_id"])
            assert case.status_code == 200, case.text
            assert case.json()["status"] == "DUPLICATE_BLOCKED"
            assert case.json()["duplicate_of_execution_id"] == "e1"
            reader.get_invoice.assert_not_awaited()
    finally:
        get_settings.cache_clear()
