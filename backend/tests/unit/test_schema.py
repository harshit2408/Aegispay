"""P1-02: PRD §8 schema on real Postgres: migration, partial unique indexes (R9), CHECKs."""

from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import Base
from app.models.core import MandatePolicy, Operator
from app.models.payables import SettlementControl, SpendLedger
from tests.seed import POLICY_ID, make_execution, seed_approved, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")


async def _insert(sm, *objs):
    async with sm() as s, s.begin():
        s.add_all(objs)


async def test_migration_creates_every_prd_table_and_the_halt_row(pg_sessionmaker):
    async with pg_sessionmaker() as s:
        query = text("select tablename from pg_tables where schemaname = 'public'")
        names = set((await s.execute(query)).scalars())
        assert set(Base.metadata.tables) <= names
        control = (await s.execute(select(SettlementControl))).scalar_one()
        assert control.halted is False


async def test_models_and_migration_have_no_drift(pg_url):
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
            )
    finally:
        await engine.dispose()
    assert diff == []


async def test_live_vendor_invoice_is_unique_until_rejected(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        s.add(make_execution("e1", invoice_number_norm="INV1042"))
    with pytest.raises(IntegrityError):
        await _insert(pg_sessionmaker, make_execution("e2", invoice_number_norm="INV1042"))
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(
            text("update invoice_executions set status='REJECTED' where execution_id='e1'")
        )
    await _insert(pg_sessionmaker, make_execution("e3", invoice_number_norm="INV1042"))


async def test_null_invoice_number_does_not_collide(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        s.add_all([make_execution("e1"), make_execution("e2")])


async def test_live_paypal_invoice_is_unique(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        s.add(make_execution("e1", paypal_invoice_id="INV2-A"))
    with pytest.raises(IntegrityError):
        await _insert(pg_sessionmaker, make_execution("e2", paypal_invoice_id="INV2-A"))


def mandate(version, **kw):
    fields = {
        "policy_id": POLICY_ID,
        "version": version,
        "raw_nl_prompt": "x",
        "compiled_json": {},
        "max_single_invoice_cents": 1,
        "daily_cap_cents": 1,
        "created_by": "op-ctl",
        "activated_by": "op-cfo",
    }
    return MandatePolicy(**{**fields, **kw})


async def test_only_one_active_mandate(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
    with pytest.raises(IntegrityError):
        await _insert(pg_sessionmaker, mandate(2, is_active=True))
    await _insert(pg_sessionmaker, mandate(3, is_active=False))


async def test_mandate_author_cannot_activate(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        s.add(Operator(operator_id="op-a", display_name="A", role="cfo", password_hash="x"))
    with pytest.raises(IntegrityError):
        await _insert(
            pg_sessionmaker, mandate(1, policy_id="p", created_by="op-a", activated_by="op-a")
        )


async def test_unknown_execution_status_is_rejected(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
    with pytest.raises(IntegrityError):
        await _insert(pg_sessionmaker, make_execution("e1", status="PAYOUT_WEBHOOK_VERIFIED"))


def ledger_row(kind, ref=None):
    return SpendLedger(
        execution_id="e1",
        policy_id=POLICY_ID,
        vendor_id="v1",
        business_date=date(2026, 11, 5),
        kind=kind,
        amount_cents=100,
        reserve_entry_id=ref,
    )


@pytest.mark.parametrize("bad", [ledger_row("RELEASE"), ledger_row("RESERVE", 1)])
async def test_spend_ledger_release_must_reference_a_reserve(pg_sessionmaker, bad):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        s.add(make_execution("e1"))
    with pytest.raises(IntegrityError):
        await _insert(pg_sessionmaker, bad)


async def test_settlement_controls_is_single_row(pg_sessionmaker):
    with pytest.raises(IntegrityError):
        await _insert(pg_sessionmaker, SettlementControl(id=False))


@pytest.mark.parametrize(
    "tamper",
    [
        "update settlement_outbox set amount_cents = 1",
        "update settlement_outbox set receiver_payer_id = 'ATTACKER123'",
        "update settlement_outbox set vendor_version = 2",
        "update invoice_executions set approved_cents = 49000",
        "update invoice_executions set replayed = true",
    ],
)
async def test_frozen_command_cannot_be_changed(pg_sessionmaker, tamper):
    """R5/R24: amount, payee and vendor version are frozen by DB triggers once written."""
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        await seed_approved(s)
    with pytest.raises(IntegrityError):
        async with pg_sessionmaker() as s, s.begin():
            await s.execute(text(tamper))


async def test_outbox_status_and_first_approved_cents_stay_writable(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        await seed_approved(s)
        s.add(make_execution("e2"))
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(text("update settlement_outbox set status = 'CLAIMED', attempts = 1"))
        await s.execute(
            text("update invoice_executions set approved_cents = 100 where execution_id = 'e2'")
        )
