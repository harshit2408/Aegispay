"""P3-13 / R25 + §13: CSRF on every state change, body caps and per-session document throttle."""

import importlib
import pkgutil
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from app import routers as app_routers
from app.auth import COOKIE, csrf_token, require_csrf, token_hash
from app.config import get_settings
from app.db import get_session
from app.main import app, create_app
from app.models.auth import OperatorSession
from app.routers.documents import MAX_PDF_BYTES
from tests.seed import seed_world

# §13: only login and the PayPal-signed webhook routes are reachable without a session.
CSRF_EXEMPT = ("/api/auth/login", "/api/webhooks/paypal/")


def _calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def test_every_state_changing_route_requires_csrf():
    # Walk every APIRouter in app.routers so a new router module cannot slip past the check.
    routers = [
        value
        for info in pkgutil.iter_modules(app_routers.__path__)
        for value in vars(importlib.import_module(f"app.routers.{info.name}")).values()
        if isinstance(value, APIRouter)
    ]
    routes = [
        route
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute) and route.methods - {"GET", "HEAD"}
    ]
    assert len(routes) > 10  # the walk really sees the operator routes
    missing = [
        route.path
        for route in routes
        if not route.path.startswith(CSRF_EXEMPT) and require_csrf not in _calls(route.dependant)
    ]
    assert missing == []


@pytest.fixture
async def anonymous():
    async def no_db():
        yield None

    application = create_app()
    application.dependency_overrides[get_session] = no_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as client:
        yield client


async def test_json_body_over_64kb_is_413_before_any_handler(anonymous):
    body = b'{"operator_id":"a","password":"' + b"x" * 65536 + b'"}'
    response = await anonymous.post(
        "/api/auth/login", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


async def test_pdf_route_allows_10mb_but_not_more(anonymous):
    def upload(size):
        return anonymous.post(
            "/api/documents",
            data={"po_id": "PO-1", "paypal_invoice_id": "INV-1"},
            files={"file": ("i.pdf", b"%PDF-" + b"0" * (size - 5), "application/pdf")},
        )

    assert (await upload(1024 * 1024)).status_code == 401  # past the cap, stopped by auth
    assert (await upload(MAX_PDF_BYTES + 65537)).status_code == 413


async def test_documents_are_throttled_at_10_per_minute_per_session(pg_sessionmaker, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/unused")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-p313-session-key-1234567")
    get_settings.cache_clear()
    now = datetime.now(UTC)
    async with pg_sessionmaker.begin() as session:
        await seed_world(session)
        for token in ("p313-one", "p313-two"):
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
    monkeypatch.setattr(app.state, "vendor_reader", object(), raising=False)
    monkeypatch.setattr(app.state, "dispatcher", object(), raising=False)

    async def upload(token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            cookies={COOKIE: token},
        ) as client:
            response = await client.post(
                "/api/documents",
                headers={"X-CSRF-Token": csrf_token(token)},
                data={"po_id": "PO-UNKNOWN", "paypal_invoice_id": "INV2-ABC"},
                files={"file": ("i.pdf", b"%PDF-1.7", "application/pdf")},
            )
            return response.status_code

    assert [await upload("p313-one") for _ in range(10)] == [404] * 10
    assert await upload("p313-one") == 429
    assert await upload("p313-two") == 404  # the limit is per session, not global
