"""PR #51 review: DB-role guard, bounded supplier view, missing-notice alerts, response limits."""

import asyncio
import hashlib
from functools import partial
from time import perf_counter
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.decision.graph import build_graph
from app.main import app
from app.models.auth import OpsAlert
from app.models.core import GoodsReceipt
from app.models.payables import AuditQueue, InvoiceExecution, SettlementOutbox, ShortPayNotice
from app.routers import vendor_sim
from app.schemas.decision import GateDecision
from app.vendor_sim.notices import _facts, create_notice, respond
from tests.unit.test_short_pay_notice import client, drafter, seed_short_pay  # noqa: F401

pytestmark = pytest.mark.usefixtures("pg_template")


async def _as_api(sm, statement, params=None):
    async with sm() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE aegis_api_role"))
        await s.execute(text(statement), params or {})


# ---- H1: the api role may record a response on a SETTLED execution, and nothing else -----------


async def test_api_role_records_a_response_on_a_settled_execution(pg_sessionmaker):
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT", status="SETTLED")
    notice_id = await create_notice(pg_sessionmaker, drafter(), "e1")
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE aegis_api_role"))
        receipt = await respond(s, notice_id, "CONTEST", "Units intact at dispatch", "op-ctl")
    assert receipt["short_pay_balance"] == "CONTESTED"
    async with pg_sessionmaker() as s:
        execution = await s.get(InvoiceExecution, "e1")
    assert (execution.status, execution.short_pay_balance) == ("SETTLED", "CONTESTED")
    assert execution.approved_cents == 40800


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE invoice_executions SET approved_cents = 1 WHERE execution_id = 'e1'",
        "UPDATE invoice_executions SET status = 'APPROVED_FOR_PAYMENT' WHERE execution_id = 'e1'",
        "UPDATE invoice_executions SET short_pay_balance = 'ACCEPTED', billed_cents = 1"
        " WHERE execution_id = 'e1'",
        "UPDATE invoice_executions SET short_pay_balance = 'ACCEPTED', row_version = 9"
        " WHERE execution_id = 'e1'",
        "UPDATE invoice_executions SET short_pay_balance = 'NONE' WHERE execution_id = 'e1'",
        "UPDATE invoice_executions SET short_pay_balance = 'NOTICE_SENT' WHERE execution_id = 'e2'",
        "UPDATE invoice_executions SET short_pay_balance = 'ACCEPTED' WHERE execution_id = 'e2'",
    ],
)
async def test_api_role_still_cannot_write_settlement_state(pg_sessionmaker, statement):
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT", status="SETTLED")
    await seed_short_pay(pg_sessionmaker, "e2")  # APPROVED_FOR_PAYMENT, balance NONE
    with pytest.raises(DBAPIError):
        await _as_api(pg_sessionmaker, statement)


async def test_api_role_cannot_insert_an_execution_with_a_sent_balance(pg_sessionmaker):
    await seed_short_pay(pg_sessionmaker)
    with pytest.raises(DBAPIError):
        await _as_api(
            pg_sessionmaker,
            "INSERT INTO invoice_executions (execution_id, document_id, langgraph_thread_id,"
            " policy_id, policy_version, detector_enabled, status, short_pay_balance)"
            " VALUES ('e9', 'doc-1', 't9', 'pol-1', 1, true, 'RECEIVED', 'ACCEPTED')",
        )


# ---- H2 / M5: bounded live status, open notices first, archive filter --------------------------


async def test_supplier_view_bounds_live_paypal_reads(pg_sessionmaker, client, monkeypatch):  # noqa: F811
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT", status="SETTLED")
    await seed_short_pay(pg_sessionmaker, "e2", status="SETTLED")
    await create_notice(pg_sessionmaker, drafter(), "e1")

    async def get_invoice(invoice_id):
        if invoice_id == "INV2-e1":
            await asyncio.sleep(30)  # hung PayPal
        raise RuntimeError("PayPal down")

    reader = AsyncMock()
    reader.get_invoice.side_effect = get_invoice
    app.state.vendor_reader = reader  # the client fixture restores it
    monkeypatch.setattr(vendor_sim, "LIVE_STATUS_TIMEOUT_S", 0.2)
    start = perf_counter()
    response = await client.get("/api/vendor-sim/invoices")
    assert response.status_code == 200, response.text
    assert perf_counter() - start < 5
    assert [row["paypal_status"] for row in response.json()] == ["SENT", "SENT"]  # stored
    assert reader.get_invoice.await_count == 2


async def test_open_notices_come_first_then_newest(pg_sessionmaker, client):  # noqa: F811
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT")
    await seed_short_pay(pg_sessionmaker, "e2")  # newer, no open notice
    await create_notice(pg_sessionmaker, drafter(), "e1")
    rows = (await client.get("/api/vendor-sim/invoices")).json()
    assert [row["paypal_invoice_id"] for row in rows] == ["INV2-e1", "INV2-e2"]


async def test_archived_rows_are_hidden(pg_sessionmaker, client, monkeypatch):  # noqa: F811
    await seed_short_pay(pg_sessionmaker)
    await seed_short_pay(pg_sessionmaker, "e2")

    async def archived(session, settings):
        return InvoiceExecution.execution_id == "e2"

    monkeypatch.setattr(vendor_sim, "archive_filter", archived)
    rows = (await client.get("/api/vendor-sim/invoices")).json()
    assert [row["paypal_invoice_id"] for row in rows] == ["INV2-e1"]


# ---- M1: a missing notice is never silently "sent" ----------------------------------------------


async def test_notice_failure_after_approval_alerts_and_keeps_the_payout(pg_sessionmaker):
    await seed_short_pay(pg_sessionmaker)

    def broken(execution, facts):
        raise RuntimeError("drafter crashed")

    graph = build_graph(
        None,
        sessions=pg_sessionmaker,
        analyze=AsyncMock(),
        reader=None,
        approve=AsyncMock(return_value=True),
        transition=AsyncMock(),
        notify=partial(create_notice, pg_sessionmaker, broken),
    )
    gate = GateDecision(
        route="SHORT_PAY",
        payable_cents=40800,
        reasons=[],
        required_approvals=0,
        allowed_actions=[],
        policy_id="pol-1",
        policy_version=1,
        gate_hash="0" * 64,
    )
    result = await graph.nodes["approve_for_payment"].bound.ainvoke(
        {"execution_id": "e1", "gate": gate}
    )
    assert result == {"approval_retry": False}
    async with pg_sessionmaker() as s:
        command = await s.scalar(select(SettlementOutbox))
        alerts = (await s.execute(select(OpsAlert.event, OpsAlert.execution_id))).all()
        notices = (await s.scalars(select(ShortPayNotice))).all()
    assert (command.amount_cents, command.status) == (40800, "PENDING")
    assert alerts == [("SHORT_PAY_NOTICE_MISSING", "e1")] and notices == []


@pytest.mark.parametrize(
    "number, norm, expected",
    [
        ("AP 1042!", "AP1042", "AP1042"),
        ("AP 1042!", None, "INV2-e1"),
        (None, "bad norm", "INV2-e1"),
    ],
)
def test_facts_fall_back_to_a_valid_identifier(number, norm, expected):
    execution = InvoiceExecution(
        execution_id="e1",
        po_id="PO-1042",
        billed_cents=48000,
        approved_cents=40800,
        vendor_invoice_number=number,
        invoice_number_norm=norm,
        paypal_invoice_id="INV2-e1",
    )
    grn = GoodsReceipt(accepted_qty=85, rejected_qty=15, inspection_note=None)
    assert _facts(execution, grn).invoice_number == expected


# ---- M3 / M4: the database enforces one bounded response ---------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE short_pay_notices SET response = 'CONTESTED'",  # second response, any role
        "UPDATE short_pay_notices SET reason = 'edited later'",
        "UPDATE short_pay_notices SET text = 'pay more'",
    ],
)
async def test_answered_notice_is_frozen_for_every_role(pg_sessionmaker, statement):
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT")
    notice_id = await create_notice(pg_sessionmaker, drafter(), "e1")
    async with pg_sessionmaker() as s, s.begin():
        await respond(s, notice_id, "ACCEPT", None, "op-ctl")
    with pytest.raises(DBAPIError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(statement))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE short_pay_notices SET reason = 'no response yet'",
        "UPDATE short_pay_notices SET response = 'ACCEPTED', responded_by = 'op-ctl',"
        " responded_at = now(), reason = repeat('x', 1001)",
        "UPDATE short_pay_notices SET text = ''",
        "UPDATE short_pay_notices SET text = repeat('x', 2001)",
    ],
)
async def test_notice_bounds_are_db_constraints(pg_sessionmaker, statement):
    await seed_short_pay(pg_sessionmaker)
    await create_notice(pg_sessionmaker, drafter(), "e1")
    with pytest.raises(DBAPIError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(statement))


# ---- LOW: request validation, audit digest, OpenAPI ---------------------------------------------


@pytest.mark.parametrize("reason", ["x" * 1001, "bad\x00byte", "line\nbreak", "bell\x07"])
async def test_bad_reason_is_rejected(pg_sessionmaker, client, reason):  # noqa: F811
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT")
    notice_id = await create_notice(pg_sessionmaker, drafter(), "e1")
    response = await client.post(
        f"/api/vendor-sim/notices/{notice_id}/respond",
        json={"action": "CONTEST", "reason": reason},
    )
    assert response.status_code == 422


async def test_audit_event_carries_the_reason_digest(pg_sessionmaker, client):  # noqa: F811
    await seed_short_pay(pg_sessionmaker, balance="NOTICE_SENT")
    notice_id = await create_notice(pg_sessionmaker, drafter(), "e1")
    reason = "Units were intact at dispatch."
    response = await client.post(
        f"/api/vendor-sim/notices/{notice_id}/respond",
        json={"action": "CONTEST", "reason": reason},
    )
    assert response.status_code == 200, response.text
    async with pg_sessionmaker() as s:
        payload = await s.scalar(
            select(AuditQueue.payload).where(AuditQueue.event_type == "SHORT_PAY_NOTICE_CONTESTED")
        )
    assert payload["reason_sha256"] == hashlib.sha256(reason.encode()).hexdigest()
    assert reason not in str(payload)


def test_openapi_documents_errors_and_role():
    paths = app.openapi()["paths"]
    post = paths["/api/vendor-sim/notices/{notice_id}/respond"]["post"]
    get = paths["/api/vendor-sim/invoices"]["get"]
    assert {"401", "403", "404", "409", "422"} <= set(post["responses"])
    assert "401" in get["responses"]
    assert post["summary"] and get["summary"]
    assert "any" in post["description"].lower()
