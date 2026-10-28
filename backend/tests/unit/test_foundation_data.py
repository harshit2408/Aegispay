"""P1-19/20: enforce trust roots and runtime privileges on real Postgres."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.core import GoodsReceipt, Operator, VendorMasterChange
from app.models.payables import Approval
from app.settlement import po_ledger, spend_ledger
from tests.seed import seed_approved, seed_world


async def world(sm):
    async with sm() as s, s.begin():
        await seed_world(s)
        await seed_approved(s)
        s.add(
            Operator(
                operator_id="op-ctl2", display_name="Second", role="controller", password_hash="x"
            )
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE goods_receipts SET accepted_qty=99, rejected_qty=1",
        "DELETE FROM goods_receipts",
        "UPDATE invoice_executions SET grn_id=NULL",
        "UPDATE invoice_executions SET paypal_invoice_id='INV2-TAMPER'",
        "UPDATE invoice_executions SET policy_version=2",
    ],
)
async def test_frozen_trust_roots_are_immutable(pg_sessionmaker, sql):
    await world(pg_sessionmaker)
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(sql))


def receipt(**kw):
    return GoodsReceipt(
        **{
            "grn_id": "grn-2",
            "po_id": "PO-1042",
            "received_qty": 1,
            "accepted_qty": 1,
            "rejected_qty": 0,
            "recorded_by": "op-rcv",
            "source": "RECEIVER_SESSION",
            "received_at": datetime(2026, 1, 1, tzinfo=UTC),
            **kw,
        }
    )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"recorded_by": "op-ctl"},
        {"recorded_by": "op-adm"},
        {"supersedes_grn_id": "grn-1", "accepted_qty": 101, "received_qty": 101},
    ],
)
async def test_grn_capacity_and_recorder_are_checked(pg_sessionmaker, fields):
    await world(pg_sessionmaker)
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            s.add(receipt(**fields))


async def test_grn_correction_supersedes_without_double_counting(pg_sessionmaker):
    await world(pg_sessionmaker)
    async with pg_sessionmaker() as s, s.begin():
        s.add(
            receipt(supersedes_grn_id="grn-1", received_qty=100, accepted_qty=90, rejected_qty=10)
        )
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            s.add(receipt(grn_id="grn-3", supersedes_grn_id="grn-1"))


@pytest.mark.parametrize("actor", ["op-ctl", "op-rcv", "op-adm"])
async def test_uploader_and_ineligible_roles_cannot_approve(pg_sessionmaker, actor):
    await world(pg_sessionmaker)
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            s.add(
                Approval(
                    approval_id="a1",
                    execution_id="e1",
                    actor=actor,
                    action="APPROVE",
                    reason="Reviewed independently",
                    gate_hash="g",
                    resume_checkpoint_id="cp",
                    resume_interrupt_id="i",
                )
            )


async def test_eligible_approval_identity_is_immutable(pg_sessionmaker):
    await world(pg_sessionmaker)
    async with pg_sessionmaker() as s, s.begin():
        s.add(
            Approval(
                approval_id="a1",
                execution_id="e1",
                actor="op-ctl2",
                action="APPROVE",
                reason="Reviewed independently",
                gate_hash="g",
                resume_checkpoint_id="cp",
                resume_interrupt_id="i",
            )
        )
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text("UPDATE approvals SET actor='op-cfo'"))


async def history(sm):
    await world(sm)
    async with sm() as s, s.begin():
        s.add(
            Operator(
                operator_id="op-va",
                display_name="Vendor admin",
                role="vendor_admin",
                password_hash="x",
            )
        )
        await s.flush()
        s.add(
            VendorMasterChange(
                change_id="c1",
                vendor_id="v1",
                old_values={"payee": "old"},
                new_values={"payee": "new"},
                requested_by="op-va",
                status="REQUESTED",
            )
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE vendor_master_changes SET new_values='{}'::jsonb",
        "DELETE FROM vendor_master_changes",
        "UPDATE vendor_master_changes SET requested_by='op-adm'",
        "UPDATE vendor_master_changes SET status='EFFECTIVE'",
        "UPDATE vendor_master_changes SET status='COOLING', approved_by='op-va', "
        "approved_at=now(), effective_at=now()+interval '72 hours'",
        "UPDATE vendor_master_changes SET status='COOLING', approved_by='op-cfo', "
        "approved_at=now(), effective_at=now()",
        "UPDATE vendor_master_changes SET status='COOLING', approved_by='op-cfo', "
        "approved_at=now()-interval '80 hours', effective_at=now()-interval '8 hours'",
    ],
)
async def test_vendor_change_history_and_cooling_are_enforced(pg_sessionmaker, sql):
    await history(pg_sessionmaker)
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(sql))


async def test_valid_vendor_approval_enters_cooling_but_cannot_skip_the_wait(pg_sessionmaker):
    await history(pg_sessionmaker)
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(
            text(
                "UPDATE vendor_master_changes SET status='COOLING', "
                "approved_by='op-cfo', approved_at=now(), "
                "effective_at=now()+interval '72 hours'"
            )
        )
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text("UPDATE vendor_master_changes SET status='EFFECTIVE'"))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE vendor_master SET version=0",
        "UPDATE vendor_master SET onboarding_evidence='{}'::jsonb",
        "UPDATE vendor_master SET trust_status='UNVERIFIED'",
    ],
)
async def test_verified_vendor_record_cannot_lose_its_evidence_or_version(pg_sessionmaker, sql):
    await world(pg_sessionmaker)
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(sql))


@pytest.mark.parametrize(
    "role,sql",
    [
        ("aegis_api_role", "UPDATE vendor_master SET paypal_payer_id='ATTACKER'"),
        ("aegis_api_role", "UPDATE settlement_outbox SET status='DONE'"),
        ("aegis_api_role", "UPDATE invoice_documents SET uploaded_by='op-cfo'"),
        ("aegis_api_role", "UPDATE invoice_executions SET status='PAYOUT_SUCCEEDED'"),
        ("aegis_api_role", "DELETE FROM payouts"),
        ("aegis_api_role", "DELETE FROM spend_ledger"),
        ("aegis_api_role", "DELETE FROM po_ledger"),
        ("aegis_api_role", "DELETE FROM execution_events"),
        ("aegis_settlement_role", "UPDATE vendor_master SET paypal_payer_id='ATTACKER'"),
        ("aegis_settlement_role", "UPDATE mandate_policies SET daily_cap_cents=999999"),
        ("aegis_settlement_role", "UPDATE mandate_policies SET policy_id=policy_id"),
        ("aegis_settlement_role", "UPDATE operators SET role='cfo'"),
        ("aegis_settlement_role", "DELETE FROM execution_events"),
    ],
)
async def test_actual_runtime_grants_deny_protected_writes(pg_sessionmaker, role, sql):
    await world(pg_sessionmaker)
    with pytest.raises(DBAPIError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(f"SET LOCAL ROLE {role}"))
            await s.execute(text(sql))


async def test_runtime_roles_can_do_their_assigned_work(pg_sessionmaker):
    await world(pg_sessionmaker)
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE aegis_api_role"))
        await s.execute(
            text(
                "INSERT INTO audit_queue(execution_id,actor,event_type,payload) "
                "VALUES('e1','system','queued','{}')"
            )
        )
        assert await s.scalar(text("SELECT count(*) FROM invoice_executions")) == 1
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE aegis_settlement_role"))
        assert (
            await s.scalar(text("SELECT policy_id FROM mandate_policies FOR NO KEY UPDATE"))
            == "pol-1"
        )
        await s.execute(text("UPDATE settlement_outbox SET status='CLAIMED', attempts=1"))
        spend = await spend_ledger.reserve(
            s, execution_id="e1", policy_id="pol-1", vendor_id="v1", amount_cents=48_000
        )
        goods = await po_ledger.reserve(s, execution_id="e1", po_id="PO-1042", amount_cents=48_000)
        await spend_ledger.release(s, reserve_entry_id=spend)
        await po_ledger.release(s, reserve_entry_id=goods)
        assert await po_ledger.committed_cents(s, "PO-1042") == 0
        await s.execute(text("DELETE FROM audit_queue"))
