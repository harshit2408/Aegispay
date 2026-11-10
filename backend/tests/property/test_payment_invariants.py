"""P2-11 generated inputs cannot choose a payee or exceed the spend cap."""

from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.core import VendorMaster
from app.models.payables import SettlementOutbox
from app.settlement import spend_ledger
from tests.seed import POLICY_ID, make_execution, seed_world
from tests.unit.test_approve import approve, decision_state
from tests.unit.test_policy_gate_db import gate_world

pytestmark = pytest.mark.usefixtures("pg_template")
GENERATED = settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@GENERATED
@given(receiver_hint=st.text(max_size=100), amount_hint=st.integers(0, 10**15))
async def test_frozen_receiver_always_comes_from_vendor_master(
    pg_sessionmaker, receiver_hint, amount_hint
):
    engine = pg_sessionmaker.kw["bind"]
    async with engine.connect() as connection, connection.begin() as transaction:
        sessions = async_sessionmaker(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        # Each generated example reruns the same trusted gate fixture inside a rollback.
        policy, _ = await gate_world.__wrapped__(sessions)
        state = await decision_state(sessions, policy)
        state["receiver_payer_id"] = receiver_hint
        state["amount_cents"] = amount_hint
        state["extraction"] = {"payee_email": receiver_hint, "total_cents": amount_hint}
        assert await approve(sessions, state)
        assert await approve(sessions, state)
        async with sessions() as session:
            command = await session.scalar(select(SettlementOutbox))
            vendor = await session.get(VendorMaster, "v1")
            assert command.receiver_payer_id == vendor.paypal_payer_id
            assert command.vendor_version == vendor.version
            assert command.amount_cents == state["gate"].payable_cents == 48000
        await transaction.rollback()


@GENERATED
@given(
    cap=st.integers(1, 10**6),
    operations=st.lists(st.tuples(st.integers(1, 10**6), st.booleans()), min_size=1, max_size=12),
)
async def test_spend_reserve_release_sequences_never_exceed_cap(pg_sessionmaker, cap, operations):
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)
    async with pg_sessionmaker() as session, session.begin() as transaction:
        await seed_world(session, daily_cap_cents=cap)
        expected = 0
        for index, (amount, release_now) in enumerate(operations):
            execution_id = f"spend-{index}"
            session.add(make_execution(execution_id))
            await session.flush()
            if expected + amount > cap:
                with pytest.raises(spend_ledger.OverCap):
                    await spend_ledger.reserve(
                        session,
                        execution_id=execution_id,
                        policy_id=POLICY_ID,
                        vendor_id="v1",
                        amount_cents=amount,
                        now=now,
                    )
            else:
                entry = await spend_ledger.reserve(
                    session,
                    execution_id=execution_id,
                    policy_id=POLICY_ID,
                    vendor_id="v1",
                    amount_cents=amount,
                    now=now,
                )
                assert (
                    await spend_ledger.reserve(
                        session,
                        execution_id=execution_id,
                        policy_id=POLICY_ID,
                        vendor_id="v1",
                        amount_cents=amount,
                        now=now,
                    )
                    == entry
                )
                expected += amount
                if release_now:
                    await spend_ledger.release(session, reserve_entry_id=entry)
                    await spend_ledger.release(session, reserve_entry_id=entry)
                    expected -= amount
            committed = await spend_ledger.committed_cents(session, POLICY_ID, now.date())
            assert committed == expected and 0 <= committed <= cap
        await transaction.rollback()
