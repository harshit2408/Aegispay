"""Role, trust-root, cooling, receipt and mandate workflow regressions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from app.auth import require_csrf, require_operator
from app.config import Settings
from app.db import get_session
from app.models.auth import OpsAlert
from app.models.core import MandatePolicy, Operator, VendorMaster, VendorMasterChange
from app.models.payables import SettlementControl
from app.routers import grns, mandates, ops, vendors
from app.schemas.decision import MandatePolicy as Policy
from app.settlement.controls import apply_vendor_changes, check_velocity
from app.settlement.prechecks import db_recheck
from tests.seed import seed_approved, seed_world


def policy(version=1):
    return Policy(
        policy_id="pol-1",
        version=version,
        max_single_invoice_cents=100000,
        daily_cap_cents=500000,
        vendor_daily_cap_cents=150000,
    )


@pytest.fixture
async def controls(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        await seed_world(session)
        row = await session.get(MandatePolicy, ("pol-1", 1))
        row.compiled_json = policy().model_dump(mode="json")
        session.add_all(
            [
                Operator(
                    operator_id=f"va{i}",
                    display_name="Vendor admin",
                    role="vendor_admin",
                    password_hash="x",
                )
                for i in (1, 2)
            ]
        )
    app = FastAPI()
    for router in (vendors.router, grns.router, ops.router, mandates.router):
        app.include_router(router)
    identity = {"id": "va1"}

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    async def actor():
        async with pg_sessionmaker() as session:
            return await session.get(Operator, identity["id"])

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[require_operator] = actor
    app.dependency_overrides[require_csrf] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        yield app, client, identity


async def test_vendor_change_requires_second_person_and_blocks_before_worker(
    controls, pg_sessionmaker
):
    _, client, identity = controls
    response = await client.post(
        "/api/vendors/v1/changes",
        json={
            "paypal_payer_id": "NEWPAYER12345",
            "verified_paypal_email": "new@x.example",
            "reason": "Verified new identity",
        },
    )
    assert response.status_code == 201
    change_id = response.json()["change_id"]
    assert (
        await client.post(
            f"/api/vendors/changes/{change_id}/approve", json={"reason": "Independent verification"}
        )
    ).status_code == 409
    identity["id"] = "op-cfo"
    approved = await client.post(
        f"/api/vendors/changes/{change_id}/approve", json={"reason": "Independent verification"}
    )
    assert approved.status_code == 200 and approved.json()["status"] == "COOLING"
    async with pg_sessionmaker() as session, session.begin():
        vendor = await session.get(VendorMaster, "v1")
        assert vendor.trust_status == "VERIFIED" and vendor.version == 1
        await seed_approved(session)
        from app.models.payables import InvoiceExecution, SettlementOutbox

        assert (
            await db_recheck(
                session,
                await session.get(InvoiceExecution, "e1"),
                await session.get(SettlementOutbox, "cmd-e1"),
            )
            == "VENDOR_NOT_VERIFIED"
        )
        await apply_vendor_changes(session, datetime.now(UTC))
        assert vendor.trust_status == "COOLING" and vendor.version == 1


async def test_elapsed_change_is_applied_once_and_versions_bump(controls, pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        now = datetime.now(UTC)
        vendor = await session.get(VendorMaster, "v1")
        session.add(
            VendorMasterChange(
                change_id="elapsed",
                vendor_id="v1",
                old_values={
                    "paypal_payer_id": vendor.paypal_payer_id,
                    "verified_paypal_email": vendor.verified_paypal_email,
                },
                new_values={
                    "paypal_payer_id": "NEWPAYER12345",
                    "verified_paypal_email": "new@x.example",
                },
                requested_by="va1",
                approved_by="va2",
                approved_at=now - timedelta(days=4),
                effective_at=now - timedelta(days=1),
                created_at=now - timedelta(days=5),
                status="COOLING",
            )
        )
        await session.flush()
        await apply_vendor_changes(session, now)
        await session.flush()
        await apply_vendor_changes(session, now)
        assert vendor.version == 2 and vendor.paypal_payer_id == "NEWPAYER12345"


async def test_receipt_role_quantity_and_correction(controls):
    _, client, identity = controls
    body = dict(
        po_id="PO-1042",
        received_qty=100,
        accepted_qty=90,
        rejected_qty=10,
        received_at="2020-01-01T00:00:00Z",
    )
    assert (await client.post("/api/grns", json=body)).status_code == 403
    identity["id"] = "op-rcv"
    first = await client.post("/api/grns", json=body)
    assert first.status_code == 201
    assert (await client.post("/api/grns", json=body)).status_code == 409
    fixed = await client.post(
        "/api/grns",
        json=body
        | {"accepted_qty": 100, "rejected_qty": 0, "supersedes_grn_id": first.json()["grn_id"]},
    )
    assert fixed.status_code == 201
    assert (
        await client.post("/api/grns", json=body | {"supersedes_grn_id": first.json()["grn_id"]})
    ).status_code == 409


async def test_mandate_compile_diff_and_independent_activation(controls, monkeypatch, pg_url):
    app, client, identity = controls
    identity["id"] = "op-ctl"
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(database_url=pg_url))
    app.state.mandate_complete = lambda *_args, **_kwargs: SimpleNamespace(
        content=policy(2).model_dump_json()
    )
    compiled = await client.post("/api/mandates/compile", json={"text": "Keep the current limits"})
    assert compiled.status_code == 201, compiled.text
    draft = compiled.json()
    assert draft["version"] == 2 and not draft["is_active"]
    url = f"/api/mandates/{draft['policy_id']}/versions/2/activate"
    assert (await client.post(url, json={"reason": "Reviewed all limits"})).status_code == 403
    identity["id"] = "op-cfo"
    assert len((await client.get("/api/mandates")).json()) == 2
    assert (await client.post(url, json={"reason": "Reviewed all limits"})).status_code == 200
    assert (await client.get("/api/mandates/active")).json()["version"] == 2
    assert (await client.post(url, json={"reason": "Reviewed all limits"})).status_code == 409


async def test_ops_role_and_alert_outbox(controls, pg_sessionmaker):
    _, client, identity = controls
    assert (
        await client.post(
            "/api/ops/settlement-halt", json={"halted": True, "reason": "Investigating incident"}
        )
    ).status_code == 403
    identity["id"] = "op-cfo"
    assert (
        await client.post(
            "/api/ops/settlement-halt", json={"halted": True, "reason": "Investigating incident"}
        )
    ).status_code == 200
    async with pg_sessionmaker() as session:
        assert (await session.get(SettlementControl, True)).halted
        assert (await session.scalar(select(OpsAlert))).event == "HALT"


async def test_runtime_api_has_no_vendor_master_write_grant(controls, pg_sessionmaker):
    async with pg_sessionmaker() as session:
        assert not await session.scalar(
            text("SELECT has_table_privilege('aegis_api_role','vendor_master','UPDATE')")
        )
        assert not await session.scalar(
            text("SELECT has_any_column_privilege('aegis_api_role','vendor_master','UPDATE')")
        )
        assert await session.scalar(
            text("SELECT has_table_privilege('aegis_settlement_role','vendor_master','UPDATE')")
        )


async def test_three_failed_items_trip_velocity(controls, pg_sessionmaker):
    from app.models.payables import Payout
    from tests.seed import make_execution

    async with pg_sessionmaker() as session, session.begin():
        for i in range(3):
            session.add(make_execution(f"velocity-{i}"))
        await session.flush()
        for i in range(3):
            session.add(
                Payout(
                    payout_id=f"p{i}",
                    execution_id=f"velocity-{i}",
                    sender_batch_id=f"b{i}",
                    receiver="x",
                    amount_cents=1,
                    item_status="FAILED",
                )
            )
        await session.flush()
        assert await check_velocity(session, 20, 300000, datetime.now(UTC))
        assert (await session.get(SettlementControl, True)).halted
