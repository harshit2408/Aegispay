"""R25 security boundary checks; HTTP + Postgres integration uses disposable fixtures."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import delete, func, select

from app.auth import COOKIE, token_hash
from app.db import get_session
from app.models.auth import AuthAttempt, OperatorSession
from app.models.core import Operator
from app.routers.auth import _throttled, router
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


async def test_five_failures_lock_the_account_for_15_minutes(auth_client, pg_sessionmaker):
    for _ in range(5):
        await auth_client.post("/api/auth/login", json={"operator_id": "alice", "password": "x"})
    async with pg_sessionmaker() as session, session.begin():
        alice = await session.get(Operator, "alice")
        remaining = alice.locked_until - datetime.now(UTC)
        assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)
        await session.execute(delete(AuthAttempt))  # the 1-minute IP/account window has passed
    response = await auth_client.post(
        "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
    )
    assert response.status_code == 401 and "set-cookie" not in response.headers


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


async def test_cross_site_login_never_creates_session_or_throttle_bucket(
    auth_client, pg_sessionmaker
):
    response = await auth_client.post(
        "/api/auth/login",
        headers={"Sec-Fetch-Site": "cross-site"},
        json={"operator_id": "alice", "password": "correct password"},
    )
    assert response.status_code == 403 and "set-cookie" not in response.headers
    async with pg_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(OperatorSession)) == 0
        assert await session.scalar(select(func.count()).select_from(AuthAttempt)) == 0


async def test_absolute_expiry_refuses_recently_active_session(auth_client, pg_sessionmaker):
    await auth_client.post(
        "/api/auth/login", json={"operator_id": "alice", "password": "correct password"}
    )
    async with pg_sessionmaker() as session, session.begin():
        row = await session.scalar(select(OperatorSession))
        row.created_at = datetime.now(UTC) - timedelta(hours=9)
        row.last_seen_at = datetime.now(UTC)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_login_rotates_token_and_csrf_and_revokes_previous_session(
    auth_client, pg_sessionmaker
):
    credentials = {"operator_id": "alice", "password": "correct password"}
    first = await auth_client.post("/api/auth/login", json=credentials)
    old_token = auth_client.cookies[COOKIE]
    second = await auth_client.post("/api/auth/login", json=credentials)
    new_token = auth_client.cookies[COOKIE]
    assert first.status_code == second.status_code == 200
    assert old_token != new_token
    assert first.json()["csrf_token"] != second.json()["csrf_token"]
    async with pg_sessionmaker() as session:
        assert await session.get(OperatorSession, token_hash(old_token)) is None
        assert await session.get(OperatorSession, token_hash(new_token)) is not None
    assert (
        await auth_client.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={old_token}"})
    ).status_code == 401
    assert (await auth_client.get("/api/auth/me")).status_code == 200
    assert (
        await auth_client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": first.json()["csrf_token"]}
        )
    ).status_code == 403


@pytest.mark.parametrize("shared", ["account", "ip"])
async def test_account_and_ip_throttles_persist_independently(pg_sessionmaker, monkeypatch, shared):
    monkeypatch.setenv("SESSION_SECRET", "synthetic-auth-throttle-secret-32-chars")
    now = datetime.now(UTC)
    for index in range(6):
        account = "same-account" if shared == "account" else f"account-{index}"
        ip = "192.0.2.1" if shared == "ip" else f"192.0.2.{index + 1}"
        async with pg_sessionmaker() as session, session.begin():
            assert await _throttled(session, account, ip, now) is (index == 5)
    async with pg_sessionmaker() as session, session.begin():
        assert not await _throttled(session, "unrelated-account", "198.51.100.1", now)


@pytest.mark.parametrize(
    ("origin", "expected_status"),
    [("https://frontend.example", 200), ("https://attacker.example", 400)],
)
async def test_cors_allows_only_configured_frontend(origin, expected_status, monkeypatch):
    from app.main import ConfiguredCORS

    monkeypatch.setenv("FRONTEND_ORIGIN", "https://frontend.example")
    application = FastAPI()
    application.add_middleware(ConfiguredCORS)

    @application.get("/probe")
    async def probe():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://api.example"
    ) as client:
        response = await client.options(
            "/probe",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
            },
        )
        assert response.status_code == expected_status
        actual = await client.get("/probe", headers={"Origin": origin})
    if expected_status == 200:
        assert response.headers["access-control-allow-origin"] == origin
        assert actual.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
    else:
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-origin" not in actual.headers
