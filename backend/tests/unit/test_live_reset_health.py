"""P2-13: owner-runner live demo reset, derived archive, and the full /api/health probes."""

import asyncio
import json
from argparse import Namespace
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings, get_settings
from app.db import get_session
from app.demo import __main__ as cli
from app.demo import reset_runner, runtime
from app.demo.seeder import OPERATORS, DemoSeed, seed_demo
from app.health import HEARTBEAT_MAX_AGE_SECONDS
from app.main import app as main_app
from app.models.core import MandatePolicy, PurchaseOrder
from app.models.payables import (
    AuditAnchor,
    AuditQueue,
    ExecutionEvent,
    InvoiceDocument,
    InvoiceExecution,
    SettlementControl,
)
from app.paypal.client import PayPalAuthError
from app.routers import auth, contracts, demo
from app.settlement import health as settlement_health

PASSWORD = "synthetic-live-reset-password"


def seed(run_id):
    return DemoSeed(
        run_id=run_id,
        vendor_payer_id="PAYERID123456",
        vendor_email="supplier@aegispay-demo.example",
        treasury_email="buyer@aegispay-demo.example",
        onboarding_evidence={"method": "synthetic", "reference": "live reset fixture"},
        operator_hashes=dict.fromkeys(OPERATORS, PasswordHasher().hash(PASSWORD)),
    )


async def control_events(sessions, event_type):
    """Control events are queued first and signed later; count both places."""
    async with sessions() as session:
        queued = (
            await session.scalars(select(AuditQueue).where(AuditQueue.event_type == event_type))
        ).all()
        signed = (
            await session.scalars(
                select(ExecutionEvent).where(ExecutionEvent.event_type == event_type)
            )
        ).all()
    return [*queued, *signed]


@pytest.fixture
async def live(pg_url, pg_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_REPLAY_MODE", "false")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-live-reset-session-secret-12")
    monkeypatch.setattr(runtime, "LIVE_RESET_POLL_SECONDS", 0.02)
    monkeypatch.setattr(reset_runner, "POLL_SECONDS", 0.02)
    get_settings.cache_clear()
    settings = get_settings()
    template = seed("base")
    await seed_demo(
        pg_sessionmaker, config=template, settings=settings, manifest_dir=tmp_path / "runs"
    )
    app = FastAPI()
    for router in (auth.router, contracts.router, demo.router):
        app.include_router(router)

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    stop = asyncio.Event()
    runner = asyncio.create_task(
        reset_runner.serve(
            pg_sessionmaker,
            stop=stop,
            template=template,
            settings=settings,
            manifest_dir=tmp_path / "runs",
        )
    )
    try:
        yield app, pg_sessionmaker, runner, stop
    finally:
        stop.set()
        await runner
        get_settings.cache_clear()


@asynccontextmanager
async def login(app, name):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        result = await client.post(
            "/api/auth/login", json={"operator_id": "demo-" + name, "password": PASSWORD}
        )
        assert result.status_code == 200, result.text
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        yield client


async def active_policy(sessions):
    async with sessions() as session:
        return await session.scalar(select(MandatePolicy.policy_id).where(MandatePolicy.is_active))


async def test_live_reset_is_fulfilled_by_the_owner_runner(live):
    app, sessions, *_ = live
    async with login(app, "admin") as admin:
        first = await admin.post("/api/demo/reset")
        assert first.status_code == 200, first.text
        one = first.json()
        assert one["replayed"] is False
        assert one["previous_run_id"] == "base"
        assert runtime.RUN_ID.fullmatch(one["run_id"])
        second = await admin.post("/api/demo/reset")
        assert second.status_code == 200, second.text
        two = second.json()
        assert two["previous_run_id"] == one["run_id"]
        assert two["run_id"] != one["run_id"]
    assert await active_policy(sessions) == "demo-" + two["run_id"]
    async with sessions() as session:
        pos = set((await session.scalars(select(PurchaseOrder.po_id))).all())
    for run in ("base", one["run_id"], two["run_id"]):
        assert {f"PO-1042-{run}", f"PO-1043-{run}", f"PO-1044-{run}"} <= pos
    assert len(await control_events(sessions, runtime.RESET_REQUEST)) == 2
    assert len(await control_events(sessions, runtime.RESET_DONE)) == 2


async def test_only_admin_can_reset(live):
    app, sessions, *_ = live
    async with login(app, "controller1") as controller:
        assert (await controller.post("/api/demo/reset")).status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as anonymous:
        assert (await anonymous.post("/api/demo/reset")).status_code in (401, 403)
    assert await control_events(sessions, runtime.RESET_REQUEST) == []


async def test_failed_seed_returns_502_and_records_only_the_error_class(live, monkeypatch):
    app, sessions, *_ = live

    async def broken(*args, **kwargs):
        raise RuntimeError("secret-bearing PayPal body must not be recorded")

    monkeypatch.setattr(reset_runner, "seed_demo", broken)
    async with login(app, "admin") as admin:
        response = await admin.post("/api/demo/reset")
    assert response.status_code == 502
    assert "secret" not in response.text
    assert await active_policy(sessions) == "demo-base"
    failed = await control_events(sessions, runtime.RESET_FAILED)
    assert [row.payload["error"] for row in failed] == ["RuntimeError"]
    assert "secret" not in str(failed[0].payload)


async def test_pending_request_is_reused_and_times_out_with_504(live, monkeypatch):
    app, sessions, runner, stop = live
    stop.set()
    await runner  # no runner is serving: the request stays pending
    monkeypatch.setattr(runtime, "LIVE_RESET_WAIT_SECONDS", 0.15)
    async with login(app, "admin") as admin:
        assert (await admin.post("/api/demo/reset")).status_code == 504
        assert (await admin.post("/api/demo/reset")).status_code == 504
    async with sessions() as session:
        pending = await runtime.pending_reset(session)
        await session.rollback()
    assert pending is not None
    # The second call waited on the same request instead of stacking another one.
    assert len(await control_events(sessions, runtime.RESET_REQUEST)) == 1


async def test_stale_request_is_not_pending(live):
    _, sessions, runner, stop = live
    stop.set()
    await runner
    async with sessions() as session, session.begin():
        session.add(
            AuditQueue(
                execution_id="demo-control",
                actor="demo-admin",
                event_type=runtime.RESET_REQUEST,
                payload={"request_id": "a" * 32, "session_hash": "x"},
                ts=datetime.now(UTC) - runtime.RESET_STALE - timedelta(seconds=1),
            )
        )
    async with sessions() as session:
        assert await runtime.pending_reset(session) is None
        await session.rollback()


async def test_reset_requests_are_throttled_per_session(live):
    app, *_ = live
    async with login(app, "admin") as admin:
        statuses = [(await admin.post("/api/demo/reset")).status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


async def test_completed_runs_leave_the_grid_but_in_flight_cases_stay(live):
    app, sessions, *_ = live
    cases = (("old-done", "SETTLED"), ("old-paying", "PAYOUT_SUBMITTED"))
    async with sessions() as session, session.begin():
        for name, _ in cases:
            session.add(
                InvoiceDocument(
                    document_id=name,
                    source="SEEDED_INBOX",
                    sha256="0" * 64,
                    storage_path="/synthetic/" + name,
                    uploaded_by="demo-controller1",
                )
            )
        await session.flush()
        for name, status in cases:
            session.add(
                InvoiceExecution(
                    execution_id=name,
                    document_id=name,
                    po_id="PO-1042-base",
                    vendor_id="demo-supplier",
                    langgraph_thread_id=name,
                    policy_id="demo-base",
                    policy_version=1,
                    detector_enabled=True,
                    status=status,
                )
            )
    async with login(app, "controller1") as controller:
        rows = (await controller.get("/api/executions")).json()
        assert {row["execution_id"] for row in rows} == {"old-done", "old-paying"}
    async with login(app, "admin") as admin:
        assert (await admin.post("/api/demo/reset")).status_code == 200
    async with login(app, "controller1") as controller:
        rows = (await controller.get("/api/executions")).json()
    assert {row["execution_id"] for row in rows} == {"old-paying"}
    async with sessions() as session:  # archived rows still exist; only the grid hides them
        assert await session.get(InvoiceExecution, "old-done") is not None


async def test_cli_serve_flag_needs_database_and_once_is_idle_without_a_request(
    pg_sessionmaker, monkeypatch, tmp_path
):
    path = tmp_path / "operators.json"
    path.write_text(seed("base").model_dump_json(), encoding="utf-8")
    settings = Settings(
        _env_file=None, database_url="postgresql+asyncpg://unused", demo_enabled=True
    )
    monkeypatch.setattr(cli, "Settings", lambda **kw: settings)
    monkeypatch.setattr(cli, "get_sessionmaker", lambda url: pg_sessionmaker)
    args = Namespace(
        config=path,
        manifest_dir=tmp_path / "runs",
        apply_db=False,
        send_invoices=False,
        serve_resets=True,
        once=True,
    )
    with pytest.raises(ValueError, match="also requires --apply-db"):
        await cli.run(args)
    args.apply_db = True
    assert await cli.run(args) == {"handled_request": None}


# ---- /api/health -------------------------------------------------------------------------------


@pytest.fixture
async def health_client(pg_sessionmaker):
    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    main_app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_app), base_url="http://test"
    ) as client:
        yield client
    main_app.dependency_overrides.clear()


async def report(sessions, *, age=0, **overrides):
    details = {
        "treasury_token": True,
        "vendor_token": True,
        "treasury_webhook": True,
        "vendor_webhook": True,
        "accounts_distinct": True,
        **overrides,
    }
    when = datetime.now(UTC) - timedelta(seconds=age)
    async with sessions() as session, session.begin():
        await session.execute(text("DELETE FROM service_health"))
        await session.execute(
            text("INSERT INTO service_health VALUES ('settlement', :t, CAST(:d AS jsonb))"),
            {"t": when, "d": json.dumps(details)},
        )


async def test_health_without_settlement_report_is_degraded_not_an_error(health_client):
    response = await health_client.get("/api/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded" and body["db"] is True
    assert body["settlement_alive"] is False
    assert set(body["checks"].values()) == {None}
    assert body["halted"] is False
    assert body["last_anchor_age_seconds"] is None


async def test_health_ok_when_every_probe_passes(health_client, pg_sessionmaker):
    await report(pg_sessionmaker)
    async with pg_sessionmaker() as session, session.begin():
        session.add(AuditAnchor(event_seq=1, head_hash="h" * 64, external_ref="ref"))
    response = await health_client.get("/api/health")
    body = response.json()
    assert response.status_code == 200 and body["status"] == "ok"
    assert body["settlement_alive"] is True
    assert all(value is True for value in body["checks"].values())
    assert body["halted"] is False
    assert isinstance(body["last_anchor_age_seconds"], int)
    assert "@" not in response.text  # no account email or identifier is ever disclosed


@pytest.mark.parametrize(
    "failing",
    ["treasury_token", "vendor_token", "treasury_webhook", "vendor_webhook", "accounts_distinct"],
)
async def test_health_degrades_for_each_failed_probe(health_client, pg_sessionmaker, failing):
    await report(pg_sessionmaker, **{failing: False})
    response = await health_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded" and body["checks"][failing] is False


async def test_health_ignores_a_stale_settlement_report(health_client, pg_sessionmaker):
    await report(pg_sessionmaker, age=HEARTBEAT_MAX_AGE_SECONDS + 30)
    body = (await health_client.get("/api/health")).json()
    assert body["status"] == "degraded" and body["settlement_alive"] is False
    assert set(body["checks"].values()) == {None}  # stale values are never presented as current


async def test_health_degrades_while_settlement_is_halted(health_client, pg_sessionmaker):
    await report(pg_sessionmaker)
    async with pg_sessionmaker() as session, session.begin():
        control = await session.get(SettlementControl, True)
        control.halted = True
    body = (await health_client.get("/api/health")).json()
    assert body["status"] == "degraded" and body["halted"] is True


async def test_service_health_privileges_match_the_process_split(pg_sessionmaker):
    write = text(
        "INSERT INTO service_health VALUES ('settlement', now(), '{}'::jsonb)"
        " ON CONFLICT (service) DO UPDATE SET checked_at = now(), details = '{}'::jsonb"
    )
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE aegis_settlement_role"))
        await session.execute(write)
        await session.execute(write)  # upsert path, as the settlement loop uses
        assert await session.scalar(text("SELECT count(*) FROM service_health")) == 1
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE aegis_api_role"))
        assert await session.scalar(text("SELECT count(*) FROM service_health")) == 1
    for statement in (write, text("DELETE FROM service_health")):
        with pytest.raises(DBAPIError):
            async with pg_sessionmaker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE aegis_api_role"))
                await session.execute(statement)


# ---- settlement-side probes --------------------------------------------------------------------


class FakePayPal:
    def __init__(self, *, token=True, webhooks=("WH-1",), fail_get=False):
        self.token, self.webhooks, self.fail_get = token, webhooks, fail_get

    async def verify_token(self):
        if not self.token:
            raise PayPalAuthError("rejected")

    async def get(self, path, **kwargs):
        assert path == "/v1/notifications/webhooks"
        if self.fail_get:
            raise RuntimeError("boom")
        return SimpleNamespace(body={"webhooks": [{"id": i} for i in self.webhooks]})


def probe_settings(**kw):
    return SimpleNamespace(
        **{
            "treasury_account_email": "Buyer@Example.test",
            "vendor_account_email": "supplier@example.test",
            "treasury_webhook_id": "WH-1",
            "vendor_webhook_id": "WH-1",
            **kw,
        }
    )


async def test_probe_reports_each_check_independently():
    ok = await settlement_health.probe(FakePayPal(), FakePayPal(), probe_settings())
    assert ok == dict.fromkeys(ok, True) and len(ok) == 5
    bad = await settlement_health.probe(
        FakePayPal(token=False, webhooks=()),
        FakePayPal(token=False, fail_get=True),
        probe_settings(vendor_account_email=" buyer@example.TEST "),
    )
    assert bad == dict.fromkeys(bad, False)


async def test_probe_treats_unset_webhook_ids_as_unregistered():
    result = await settlement_health.probe(
        FakePayPal(), FakePayPal(), probe_settings(treasury_webhook_id="", vendor_webhook_id=" ")
    )
    assert (result["treasury_webhook"], result["vendor_webhook"]) == (False, False)
    assert result["treasury_token"] and result["vendor_token"] and result["accounts_distinct"]


async def test_record_upserts_one_row(pg_sessionmaker):
    for value in (True, False):
        await settlement_health.record(pg_sessionmaker, {"treasury_token": value})
    async with pg_sessionmaker() as session:
        rows = (await session.execute(text("SELECT service, details FROM service_health"))).all()
    assert [(r.service, r.details) for r in rows] == [("settlement", {"treasury_token": False})]


async def test_probe_loop_survives_failures_and_stops_on_event(monkeypatch):
    calls = []
    stop = asyncio.Event()

    async def explode(sessions, details):
        calls.append(details)
        if len(calls) == 2:
            stop.set()
        raise ConnectionError("database restarting")

    monkeypatch.setattr(settlement_health, "record", explode)
    monkeypatch.setattr(settlement_health, "INTERVAL_SECONDS", 0.01)
    await asyncio.wait_for(
        settlement_health.run_forever(None, FakePayPal(), FakePayPal(), probe_settings(), stop), 5
    )
    assert len(calls) == 2  # the first failure did not end the loop


async def test_probe_timeout_marks_a_hung_paypal_call_unhealthy(monkeypatch):
    class Hung(FakePayPal):
        async def verify_token(self):
            await asyncio.sleep(3600)

        async def get(self, path, **kwargs):
            await asyncio.sleep(3600)

    monkeypatch.setattr(settlement_health, "PROBE_TIMEOUT_SECONDS", 0.05)
    result = await asyncio.wait_for(
        settlement_health.probe(Hung(), Hung(), probe_settings()), timeout=5
    )
    assert (result["treasury_token"], result["vendor_webhook"]) == (False, False)
    assert result["accounts_distinct"] is True


async def test_resumed_request_reuses_its_run_when_the_outcome_write_fails(
    live, monkeypatch, tmp_path
):
    _, sessions, runner, stop = live
    stop.set()
    await runner
    template, settings = seed("base"), get_settings()
    async with sessions() as session:
        await runtime.request_live_reset(session, "demo-admin", bucket="resume-bucket")
    real, failures = reset_runner._record, []

    async def flaky(sessions, kind, payload):
        if kind == runtime.RESET_DONE and not failures:
            failures.append(payload)
            raise ConnectionError("database restarting")
        await real(sessions, kind, payload)

    monkeypatch.setattr(reset_runner, "_record", flaky)
    options = {"template": template, "settings": settings, "manifest_dir": tmp_path / "resume"}
    with pytest.raises(ConnectionError):
        await reset_runner.process_pending(sessions, **options)
    assert await reset_runner.process_pending(sessions, **options) is not None
    done = await control_events(sessions, runtime.RESET_DONE)
    started = await control_events(sessions, runtime.RESET_STARTED)
    assert len(started) == 1 and len(done) == 1
    assert done[0].payload["run_id"] == started[0].payload["run_id"] == failures[0]["run_id"]
    async with sessions() as session:
        pos = (await session.scalars(select(PurchaseOrder.po_id))).all()
    assert len([po for po in pos if po.startswith("PO-1042-")]) == 2  # base + exactly one reset
    async with sessions() as session:
        assert await runtime.pending_reset(session) is None
        await session.rollback()


async def test_only_one_reset_runner_may_hold_the_lock(live):
    _, sessions, runner, stop = live
    with pytest.raises(RuntimeError, match="already active"):
        async with reset_runner.exclusive(sessions):
            pass  # the fixture's serve() holds the lock
    stop.set()
    await runner
    async with reset_runner.exclusive(sessions):  # free again after the runner stopped
        pass


async def test_concurrent_reset_waiters_are_capped(live, monkeypatch):
    app, *_ = live
    monkeypatch.setattr(demo, "WAITERS", asyncio.Semaphore(0))
    async with login(app, "admin") as admin:
        assert (await admin.post("/api/demo/reset")).status_code == 429


def test_settlement_worker_runs_the_health_loop():
    source = (Path(__file__).parents[2] / "app" / "settlement" / "worker.py").read_text("utf-8")
    assert "health.run_forever(sessions, treasury, vendor, settings, stop)" in source
