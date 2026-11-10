"""R25 security boundary checks; HTTP + Postgres integration uses disposable fixtures."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import select

from app.auth import COOKIE, token_hash
from app.db import get_session
from app.models.auth import OperatorSession
from app.models.core import Operator
from app.routers.auth import router
from app.routers.grns import ReceiptRequest


@pytest.fixture
async def auth_client(pg_sessionmaker, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "a" * 32)
    async with pg_sessionmaker() as session, session.begin():
        session.add(
            Operator(
                operator_id="alice",
                display_name="Alice",
                role="controller",
                password_hash=PasswordHasher().hash("correct password"),
            )
        )
    app = FastAPI()
    app.include_router(router)

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        yield client


async def test_opaque_session_csrf_revocation_and_fresh_role(auth_client, pg_sessionmaker):
    response = await auth_client.post(
        "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    token = auth_client.cookies[COOKIE]
    async with pg_sessionmaker() as session:
        row = await session.get(OperatorSession, token_hash(token))
        assert row is not None and row.token_hash != token
        operator = await session.get(Operator, "alice")
        operator.role = "admin"
        await session.commit()
    assert (await auth_client.get("/api/auth/me")).json()["role"] == "admin"
    assert (await auth_client.post("/api/auth/logout")).status_code == 403
    assert (
        await auth_client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": response.json()["csrf_token"]}
        )
    ).status_code == 204
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_idle_expiry_and_disabled_operator(auth_client, pg_sessionmaker):
    await auth_client.post(
        "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
    )
    async with pg_sessionmaker() as session:
        row = await session.scalar(select(OperatorSession))
        row.last_seen_at = datetime.now(UTC) - timedelta(minutes=31)
        await session.commit()
    assert (await auth_client.get("/api/auth/me")).status_code == 401
    await auth_client.post(
        "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
    )
    async with pg_sessionmaker() as session:
        (await session.get(Operator, "alice")).disabled = True
        await session.commit()
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_persistent_throttle_and_account_lockout(auth_client):
    for _ in range(5):
        assert (
            await auth_client.post(
                "/api/auth/login", json={"operator_id": "alice", "password": "wrong"}
            )
        ).status_code == 401
    assert (
        await auth_client.post(
            "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
        )
    ).status_code == 429


@pytest.mark.parametrize(
    "changes",
    [
        {"accepted_qty": 2},
        {"received_at": datetime.now(UTC) + timedelta(days=1)},
        {"received_at": datetime.now()},
        {"accepted_qty": True},
    ],
)
def test_receipt_rejects_untrusted_quantities_and_dates(changes):
    data = dict(
        po_id="PO-1",
        received_qty=1,
        accepted_qty=1,
        rejected_qty=0,
        received_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError):
        ReceiptRequest(**(data | changes))


async def test_expired_lockout_does_not_relock_on_one_failure(auth_client, pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        alice = await session.get(Operator, "alice")
        alice.failed_logins = 5
        alice.locked_until = datetime.now(UTC) - timedelta(minutes=1)
    assert (
        await auth_client.post("/api/auth/login", json={"operator_id": "alice", "password": "bad"})
    ).status_code == 401
    async with pg_sessionmaker() as session:
        alice = await session.get(Operator, "alice")
        assert alice.failed_logins == 1 and alice.locked_until is None
    assert (
        await auth_client.post(
            "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
        )
    ).status_code == 200
