"""R1, R3, R5, R11, R17 and R24 at the atomic graph-to-settlement boundary."""

import asyncio

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.decision.approve import approve_for_payment
from app.decision.policy_gate import evaluate
from app.models.payables import InvoiceExecution, POLedger, SettlementOutbox, SpendLedger
from app.schemas.decision import RiskAssessment
from app.settlement import po_ledger, transitions
from tests.seed import PAYER_ID, make_execution
from tests.unit import test_policy_gate_db
from tests.unit.test_policy_gate import forensics, report

gate_world = test_policy_gate_db.gate_world


@pytest.fixture
async def api_sessions(pg_url):
    engine = create_async_engine(pg_url)

    @event.listens_for(engine.sync_engine, "begin")
    def restrict(connection):
        connection.exec_driver_sql("SET LOCAL ROLE aegis_api_role")

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def decision_state(sessions, policy, execution_id="e1"):
    match = report(
        invoice_number_norm="INV1042" if execution_id == "e1" else "BILL9000",
        paypal_invoice_id="INV2-ABC" if execution_id == "e1" else "INV2-DEF",
    )
    risk = RiskAssessment(score=0, contributions=[])
    forensic = forensics()
    async with sessions() as session:
        gate = await evaluate(
            session,
            execution_id=execution_id,
            policy=policy,
            match=match,
            risk=risk,
            forensics=forensic,
        )
    assert gate.route == "AUTO_SETTLE"
    return {"policy": policy, "match": match, "risk": risk, "forensics": forensic, "gate": gate}


async def approve(sessions, state, execution_id="e1"):
    return await approve_for_payment(
        sessions, transitions.apply, po_ledger.reserve, execution_id, state
    )


@pytest.mark.parametrize("replayed", [False, True])
async def test_frozen_approval_is_idempotent_and_replay_never_reserves(
    pg_sessionmaker, gate_world, api_sessions, replayed
):
    policy, _ = gate_world
    execution_id = "e2" if replayed else "e1"
    if replayed:
        async with pg_sessionmaker() as session, session.begin():
            session.add(
                make_execution(
                    execution_id,
                    status="ANALYZING",
                    replayed=True,
                    policy_version=2,
                    paypal_invoice_id="INV2-DEF",
                    invoice_number_norm="BILL9000",
                )
            )
    state = await decision_state(pg_sessionmaker, policy, execution_id)
    assert await approve(api_sessions, state, execution_id)
    assert await approve(api_sessions, state, execution_id)
    async with pg_sessionmaker() as session:
        execution = await session.get(InvoiceExecution, execution_id)
        assert (execution.status, execution.approved_cents, execution.row_version) == (
            "APPROVED_FOR_PAYMENT",
            48000,
            1,
        )
        outbox = await session.scalar(select(SettlementOutbox))
        reserved = await session.scalar(select(func.sum(POLedger.amount_cents)))
        assert await session.scalar(select(func.count()).select_from(SpendLedger)) == 0
        if replayed:
            assert outbox is None and reserved is None
        else:
            assert (outbox.receiver_payer_id, outbox.vendor_version, outbox.amount_cents) == (
                PAYER_ID,
                execution.vendor_version,
                48000,
            )
            assert reserved == 48000


async def test_concurrent_approvals_cannot_exceed_accepted_value(
    pg_sessionmaker, gate_world, api_sessions
):
    policy, _ = gate_world
    async with pg_sessionmaker() as session, session.begin():
        session.add(
            make_execution(
                "e2",
                status="ANALYZING",
                policy_version=2,
                paypal_invoice_id="INV2-DEF",
                invoice_number_norm="BILL9000",
            )
        )
    first, second = await asyncio.gather(
        decision_state(pg_sessionmaker, policy, "e1"),
        decision_state(pg_sessionmaker, policy, "e2"),
    )
    assert sorted(
        await asyncio.gather(
            approve(api_sessions, first, "e1"), approve(api_sessions, second, "e2")
        )
    ) == [False, True]
    async with pg_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(SettlementOutbox)) == 1
        assert await session.scalar(select(func.sum(POLedger.amount_cents))) == 48000


async def test_locked_regate_rejects_stale_halt_and_missing_index_binding(
    pg_sessionmaker, gate_world, api_sessions
):
    policy, _ = gate_world
    state = await decision_state(pg_sessionmaker, policy)
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("UPDATE settlement_controls SET halted=true"))
    assert not await approve(api_sessions, state)
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("UPDATE settlement_controls SET halted=false"))
        await session.execute(
            update(InvoiceExecution)
            .where(InvoiceExecution.execution_id == "e1")
            .values(invoice_number_norm=None)
        )
    assert not await approve(api_sessions, state)
    async with pg_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(SettlementOutbox)) == 0
        assert await session.scalar(select(func.count()).select_from(POLedger)) == 0
