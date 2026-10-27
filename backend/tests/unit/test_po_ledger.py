"""R17: real Postgres locks, replay safety, releases and corrected acceptance."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.core import GoodsReceipt
from app.models.payables import POLedger
from app.settlement import po_ledger
from tests.seed import make_execution, seed_approved, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")


async def world(sm, executions=1):
    async with sm() as s, s.begin():
        await seed_world(s)
        await seed_approved(s, "e0")
        s.add_all(make_execution(f"e{i}") for i in range(1, executions))


async def reserve(sm, execution_id, amount_cents=48000):
    async with sm() as s, s.begin():
        return await po_ledger.reserve(
            s, execution_id=execution_id, po_id="PO-1042", amount_cents=amount_cents
        )


async def test_parallel_reserves_cannot_rebill_the_same_accepted_goods(pg_sessionmaker):
    await world(pg_sessionmaker, executions=12)
    results = await asyncio.gather(
        *(reserve(pg_sessionmaker, f"e{i}", 10000) for i in range(12)), return_exceptions=True
    )
    assert sum(type(r) is int for r in results) == 4
    assert sum(isinstance(r, po_ledger.OverConsumption) for r in results) == 8
    async with pg_sessionmaker() as s:
        assert await po_ledger.committed_cents(s, "PO-1042") == 40000


async def test_retry_same_execution_is_one_reserve_and_mismatched_command_is_rejected(
    pg_sessionmaker,
):
    await world(pg_sessionmaker)
    ids = await asyncio.gather(*(reserve(pg_sessionmaker, "e0") for _ in range(3)))
    assert len(set(ids)) == 1
    with pytest.raises(ValueError, match="differs"):
        await reserve(pg_sessionmaker, "e0", 47999)
    async with pg_sessionmaker() as s:
        assert len((await s.execute(select(POLedger))).scalars().all()) == 1


async def test_confirmed_failure_release_is_idempotent_and_capacity_can_be_reused(pg_sessionmaker):
    await world(pg_sessionmaker, executions=2)
    entry = await reserve(pg_sessionmaker, "e0")

    async def release():
        async with pg_sessionmaker() as s, s.begin():
            return await po_ledger.release(s, reserve_entry_id=entry)

    ids = await asyncio.gather(release(), release(), release())
    assert len(set(ids)) == 1
    async with pg_sessionmaker() as s:
        released = await s.get(POLedger, ids[0])
        assert (released.qty, released.amount_cents, released.reserve_entry_id) == (
            100,
            48000,
            entry,
        )
        assert await po_ledger.committed_cents(s, "PO-1042") == 0
    await reserve(pg_sessionmaker, "e1")
    with pytest.raises(ValueError):
        async with pg_sessionmaker() as s, s.begin():
            await po_ledger.release(s, reserve_entry_id=ids[0])


async def test_superseded_receipt_does_not_add_acceptance_twice(pg_sessionmaker):
    await world(pg_sessionmaker)
    async with pg_sessionmaker() as s, s.begin():
        s.add(
            GoodsReceipt(
                grn_id="correction",
                po_id="PO-1042",
                supersedes_grn_id="grn-1",
                received_qty=100,
                accepted_qty=50,
                rejected_qty=50,
                recorded_by="op-rcv",
                source="RECEIVER_SESSION",
                received_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
    with pytest.raises(po_ledger.OverConsumption):
        await reserve(pg_sessionmaker, "e0", 24001)
    await reserve(pg_sessionmaker, "e0", 24000)


@pytest.mark.parametrize("bad", [0, -1, True, "100", 1.5])
async def test_amount_is_a_positive_integer_only(pg_sessionmaker, bad):
    async with pg_sessionmaker() as s, s.begin():
        with pytest.raises(ValueError):
            await po_ledger.reserve(s, execution_id="missing", po_id="missing", amount_cents=bad)
