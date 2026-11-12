"""Local replay acceptance through HTTP admission, real Postgres graph and operator decisions."""

from functools import partial
from time import perf_counter

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select

from app.agents.provider import postgres_dsn
from app.config import Settings, get_settings
from app.db import get_session
from app.decision.analyze import build_analyzer
from app.decision.approve import approve_for_payment
from app.decision.dispatch import Dispatcher
from app.decision.graph import build_graph
from app.demo import runtime
from app.demo.runtime import DemoUnavailable, ReplayReader
from app.demo.seeder import OPERATORS, DemoSeed, seed_demo
from app.models.payables import (
    InvoiceExecution,
    POLedger,
    SettlementOutbox,
    ShortPayNotice,
    SpendLedger,
)
from app.routers import auth, contracts, demo
from app.settlement.po_ledger import reserve
from app.settlement.transitions import apply
from app.vendor_sim.notices import create_notice, governed_drafter


@pytest.fixture
async def replay_app(pg_url, pg_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_REPLAY_MODE", "true")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-replay-session-secret-key-123")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    settings = get_settings()
    password = "local-replay-fixture-password"
    encoded = PasswordHasher().hash(password)
    await seed_demo(
        pg_sessionmaker,
        config=DemoSeed(
            run_id="replay",
            vendor_payer_id="PAYERID123456",
            vendor_email="supplier@aegispay-demo.example",
            treasury_email="buyer@aegispay-demo.example",
            onboarding_evidence={"method": "synthetic replay", "reference": "local fixture"},
            operator_hashes={name: encoded for name in OPERATORS},
        ),
        settings=Settings(database_url=pg_url, demo_enabled=True, demo_replay_mode=False),
        manifest_dir=tmp_path / "manifest",
    )
    app = FastAPI()
    for router in (auth.router, contracts.router, demo.router):
        app.include_router(router)

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        recorded = ReplayReader(pg_sessionmaker, settings.storage_path)
        graph = build_graph(
            saver,
            sessions=pg_sessionmaker,
            analyze=build_analyzer(pg_sessionmaker, settings),
            reader=None,
            replay_reader=recorded,
            transition=apply,
            notify=partial(create_notice, pg_sessionmaker, governed_drafter(settings)),
            approve=partial(approve_for_payment, pg_sessionmaker, apply, reserve),
        )
        dispatcher = Dispatcher(graph, pg_sessionmaker, postgres_dsn(pg_url))
        app.state.dispatcher = dispatcher
        app.state.run_case = dispatcher.run
        try:
            yield app, password, dispatcher, recorded
        finally:
            get_settings.cache_clear()


async def login(client, name, password):
    result = await client.post(
        "/api/auth/login",
        json={"operator_id": "demo-" + name, "password": password},
    )
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]


async def test_s1_s2_s3_s4_replay_decisions_without_live_money(
    replay_app, pg_sessionmaker, record_property
):
    app, password, dispatcher, recorded = replay_app
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="https://test") as controller,
        httpx.AsyncClient(transport=transport, base_url="https://test") as admin,
        httpx.AsyncClient(transport=transport, base_url="https://test") as cfo,
    ):
        await login(controller, "controller1", password)
        await login(admin, "admin", password)
        await login(cfo, "cfo", password)
        reset = await admin.post("/api/demo/reset")
        assert reset.status_code == 200, reset.text
        assert reset.json()["replayed"]
        admitted = {}
        for scenario, route, payable in (
            ("S1", "AUTO_SETTLE", 48000),
            ("S2", "SHORT_PAY", 42500),
            ("S3", "FREEZE", 40000),
        ):
            response = await controller.post(f"/api/demo/scenarios/{scenario}/run")
            assert response.status_code == 202, response.text
            execution_id = response.json()["execution_id"]
            admitted[scenario] = execution_id
            snapshot = await dispatcher.snapshot(execution_id)
            gate = snapshot.values["gate"]
            assert gate.route == route
            assert gate.payable_cents == payable
            if scenario == "S2":
                short_pay = snapshot.values["short_pay"]
                assert short_pay.expected_cost_pay_and_claim_cents == 6592
                assert short_pay.expected_cost_short_pay_cents == 1500
                async with pg_sessionmaker() as session:  # §12.4: replay → template, no LLM
                    notice = await session.scalar(
                        select(ShortPayNotice).where(ShortPayNotice.execution_id == execution_id)
                    )
                    row = await session.get(InvoiceExecution, execution_id)
                assert notice.used_fallback and notice.delta_cents == 7500
                assert "$75.00" in notice.text and row.approved_cents == 42500
            if scenario == "S3":
                assert snapshot.values["risk"].score == 100
                assert gate.required_approvals == 2
                rejected = await cfo.post(
                    f"/api/executions/{execution_id}/decisions",
                    json={
                        "action": "REJECT",
                        "reason": "Rejected forged invoice evidence",
                        "gate_hash": gate.gate_hash,
                    },
                )
                assert rejected.status_code == 200, rejected.text
                async with pg_sessionmaker() as session:
                    assert (await session.get(InvoiceExecution, execution_id)).status == "REJECTED"
        start = perf_counter()
        duplicate = await controller.post("/api/demo/scenarios/S4/run")
        elapsed = (perf_counter() - start) * 1000
        record_property("replay_s4_latency_ms", round(elapsed, 2))
        assert duplicate.status_code == 202, duplicate.text
        assert elapsed < 1000
        assert duplicate.json()["duplicate_of_execution_id"] == admitted["S1"]
        disabled = await admin.post("/api/demo/forensics-detector", json={"enabled": False})
        assert disabled.status_code == 200, disabled.text
        killed = await controller.post("/api/demo/scenarios/S3/run")
        assert killed.status_code == 202, killed.text
        snapshot = await dispatcher.snapshot(killed.json()["execution_id"])
        assert snapshot.values["gate"].route == "FREEZE"
        assert snapshot.values["forensics"].detector_enabled is False
        assert snapshot.values["forensics"].hidden_segments == []
        assert "HIGH_RISK" in snapshot.values["gate"].reasons
        assert "UNVERIFIED_SOURCE" in snapshot.values["gate"].reasons
        with pytest.raises(DemoUnavailable):
            await recorded.get_invoice("unbound", execution_id=admitted["S1"])
        async with pg_sessionmaker() as session:
            for model in (SpendLedger, POLedger, SettlementOutbox):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            for scenario in ("S1", "S2"):
                row = await session.get(InvoiceExecution, admitted[scenario])
                assert row.status == "APPROVED_FOR_PAYMENT"
                assert row.replayed
        assert (await controller.post("/api/demo/reset")).status_code == 403
        admin.headers.pop("X-CSRF-Token")
        assert (await admin.post("/api/demo/reset")).status_code == 403


async def test_demo_disabled_and_live_mode_fail_closed(replay_app, monkeypatch):
    app, password, _, _ = replay_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await login(client, "admin", password)
        monkeypatch.setenv("DEMO_REPLAY_MODE", "false")
        get_settings.cache_clear()
        monkeypatch.setattr(runtime, "LIVE_RESET_WAIT_SECONDS", 0.05)
        # Live reset is owner-runner work: with no runner the request stays pending (504)...
        assert (await client.post("/api/demo/reset")).status_code == 504
        # ...and live mode still refuses the replay-only controls.
        detector = await client.post("/api/demo/forensics-detector", json={"enabled": True})
        assert detector.status_code == 503
        monkeypatch.setenv("DEMO_ENABLED", "false")
        get_settings.cache_clear()
        assert (await client.post("/api/demo/reset")).status_code == 404
