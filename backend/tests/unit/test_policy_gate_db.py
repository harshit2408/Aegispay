"""Real PostgreSQL R11/R15: current authoritative facts and eligible immutable votes."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.decision.policy_gate import evaluate
from app.models.core import GoodsReceipt, MandatePolicy, Operator, VendorMaster, VendorMasterChange
from app.models.payables import Approval
from app.schemas.decision import RiskAssessment
from tests.seed import make_execution, seed_world
from tests.unit.test_policy_gate import forensics, policy, report


@pytest.fixture
async def gate_world(pg_sessionmaker):
    now = datetime.now(UTC)
    compiled = policy(version=2)
    async with pg_sessionmaker() as session, session.begin():
        await seed_world(session)
        # New immutable policy version: do not alter the seeded compiled draft.
        await session.execute(update(MandatePolicy).values(is_active=False))
        session.add(
            MandatePolicy(
                policy_id=compiled.policy_id,
                version=2,
                raw_nl_prompt="test",
                compiled_json=compiled.model_dump(),
                max_single_invoice_cents=100000,
                daily_cap_cents=500000,
                created_by="op-ctl",
                activated_by="op-cfo",
                activated_at=now,
                is_active=True,
            )
        )
        session.add_all(
            [
                Operator(
                    operator_id="op-second",
                    display_name="Second",
                    role="controller",
                    password_hash="x",
                ),
                Operator(
                    operator_id="op-vendor-admin",
                    display_name="Vendor admin",
                    role="vendor_admin",
                    password_hash="x",
                ),
                GoodsReceipt(
                    grn_id="grn-1",
                    po_id="PO-1042",
                    received_qty=100,
                    accepted_qty=100,
                    rejected_qty=0,
                    recorded_by="op-rcv",
                    source="SEEDED",
                    received_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.flush()
        session.add(
            make_execution(
                "e1",
                status="FROZEN",
                policy_version=2,
                paypal_invoice_id="INV2-ABC",
                invoice_number_norm="INV1042",
            )
        )
        await session.execute(update(VendorMaster).values(onboarded_at=now - timedelta(days=90)))
    return compiled, now


async def current(session, compiled, now, **changes):
    return await evaluate(
        session,
        execution_id="e1",
        policy=compiled,
        match=report(payee_matches_vendor_master=False, **changes),
        risk=RiskAssessment(score=0, contributions=[]),
        forensics=forensics(),
        now=now,
    )


def vote(actor, gate_hash, *, suffix="", **changes):
    return Approval(
        **{
            "approval_id": actor + suffix,
            "execution_id": "e1",
            "actor": actor,
            "action": "APPROVE",
            "reason": "Reviewed trusted facts",
            "gate_hash": gate_hash,
            "resume_checkpoint_id": "checkpoint",
            "resume_interrupt_id": "interrupt",
            **changes,
        }
    )


async def test_hash_stable_after_votes_and_only_eligible_distinct_humans_count(
    pg_sessionmaker, gate_world
):
    compiled, now = gate_world
    async with pg_sessionmaker() as session:
        first = await current(session, compiled, now)
        assert first.required_approvals == 2 and first.route == "FREEZE"
        await session.rollback()
    async with pg_sessionmaker() as session, session.begin():
        for ineligible in ("op-ctl", "op-adm", "op-rcv"):
            with pytest.raises(IntegrityError, match="approval violates operator SoD"):
                async with session.begin_nested():
                    session.add(vote(ineligible, first.gate_hash))
                    await session.flush()
        session.add_all(
            [
                vote("op-cfo", first.gate_hash, consumed_at=now),  # consumed receipt still a vote
                vote("op-second", "stale-hash"),
            ]
        )
    async with pg_sessionmaker() as session:
        second = await current(session, compiled, now)
        assert second.gate_hash == first.gate_hash and second.route == "FREEZE"
        await session.rollback()
    async with pg_sessionmaker() as session, session.begin():
        session.add(vote("op-second", first.gate_hash, suffix="-current"))
    async with pg_sessionmaker() as session:
        final = await current(session, compiled, now)
        assert final.gate_hash == first.gate_hash and final.route == "AUTO_SETTLE"


@pytest.mark.parametrize("excluded", ["disabled", "voided"])
async def test_disabled_and_voided_votes_do_not_count(pg_sessionmaker, gate_world, excluded):
    compiled, now = gate_world
    async with pg_sessionmaker() as session:
        first = await current(session, compiled, now)
        await session.rollback()
    async with pg_sessionmaker() as session, session.begin():
        session.add(vote("op-cfo", first.gate_hash))
        session.add(
            vote(
                "op-second", first.gate_hash, **({"voided_at": now} if excluded == "voided" else {})
            )
        )
        if excluded == "disabled":
            await session.execute(
                update(Operator).where(Operator.operator_id == "op-second").values(disabled=True)
            )
    async with pg_sessionmaker() as session:
        assert (await current(session, compiled, now)).route == "FREEZE"


async def test_pending_cooling_change_blocks_before_master_update(pg_sessionmaker, gate_world):
    compiled, now = gate_world
    async with pg_sessionmaker() as session:
        first = await current(session, compiled, now)
        await session.rollback()
    async with pg_sessionmaker() as session, session.begin():
        session.add_all([vote("op-cfo", first.gate_hash), vote("op-second", first.gate_hash)])
        session.add(
            VendorMasterChange(
                change_id="change",
                vendor_id="v1",
                old_values={"email": "old@example.invalid"},
                new_values={"email": "new@example.invalid"},
                requested_by="op-vendor-admin",
                approved_by="op-cfo",
                created_at=now - timedelta(minutes=1),
                approved_at=now,
                effective_at=now + timedelta(hours=72),
                status="COOLING",
            )
        )
    async with pg_sessionmaker() as session:
        final = await current(session, compiled, now)
        assert final.gate_hash != first.gate_hash
        assert final.route == "FREEZE" and final.required_approvals == 99
        assert "VENDOR_COOLING" in final.reasons


async def test_current_grn_correction_invalidates_hash_and_old_match(pg_sessionmaker, gate_world):
    compiled, now = gate_world
    async with pg_sessionmaker() as session:
        first = await current(session, compiled, now)
        await session.rollback()
    async with pg_sessionmaker() as session, session.begin():
        session.add(
            GoodsReceipt(
                grn_id="grn-2",
                supersedes_grn_id="grn-1",
                po_id="PO-1042",
                received_qty=100,
                accepted_qty=80,
                rejected_qty=20,
                recorded_by="op-rcv",
                source="RECEIVER_SESSION",
                received_at=now,
            )
        )
    async with pg_sessionmaker() as session:
        final = await current(session, compiled, now)
        assert final.gate_hash != first.gate_hash and final.required_approvals == 99
        assert "STALE_TRUSTED_FACTS" in final.reasons
