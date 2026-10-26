"""P1-06: atomic spend cap, same-business_date release, idempotency (PRD R8, §11.4)."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text

from app.models.payables import SpendLedger
from app.settlement.spend_ledger import (
    NoActiveMandate,
    OverCap,
    business_date,
    committed_cents,
    release,
    reserve,
)
from tests.seed import POLICY_ID, make_execution, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")
NOON = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


async def _world(sm, executions=1, cap=500):
    async with sm() as s, s.begin():
        await seed_world(s, daily_cap_cents=cap)
        s.add_all(make_execution(f"e{i}") for i in range(executions))


async def _reserve(sm, execution_id, amount=100, now=NOON):
    async with sm() as s, s.begin():
        return await reserve(
            s,
            execution_id=execution_id,
            policy_id=POLICY_ID,
            vendor_id="v1",
            amount_cents=amount,
            now=now,
        )


def test_business_date_uses_cap_timezone():
    late = datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
    assert business_date(late, "UTC") == date(2026, 11, 5)
    assert business_date(late, "Asia/Kolkata") == date(2026, 11, 6)
    assert business_date(late, "America/Los_Angeles") == date(2026, 11, 5)


def test_business_date_rejects_naive_datetime():
    with pytest.raises(ValueError):
        business_date(datetime(2026, 11, 5, 12, 0), "UTC")


async def test_reserve_up_to_cap_then_over_cap(pg_sessionmaker):
    await _world(pg_sessionmaker, executions=3, cap=250)
    await _reserve(pg_sessionmaker, "e0", 100)
    await _reserve(pg_sessionmaker, "e1", 150)  # exactly at the cap
    with pytest.raises(OverCap):
        await _reserve(pg_sessionmaker, "e2", 1)
    async with pg_sessionmaker() as s:
        assert await committed_cents(s, POLICY_ID, date(2026, 11, 5)) == 250


async def test_concurrent_reservations_never_exceed_cap(pg_sessionmaker):
    await _world(pg_sessionmaker, executions=12, cap=500)
    results = await asyncio.gather(
        *(_reserve(pg_sessionmaker, f"e{i}", 100) for i in range(12)), return_exceptions=True
    )
    assert sum(isinstance(r, int) for r in results) == 5
    assert sum(isinstance(r, OverCap) for r in results) == 7
    async with pg_sessionmaker() as s:
        assert await committed_cents(s, POLICY_ID, date(2026, 11, 5)) == 500


async def test_reserve_is_idempotent_per_execution(pg_sessionmaker):
    await _world(pg_sessionmaker, cap=150)
    first = await _reserve(pg_sessionmaker, "e0", 100)
    again = await _reserve(pg_sessionmaker, "e0", 100)  # crash-recovery re-entry (R19)
    assert first == again
    async with pg_sessionmaker() as s:
        assert await committed_cents(s, POLICY_ID, date(2026, 11, 5)) == 100


async def test_release_lands_on_reserve_business_date_and_frees_cap(pg_sessionmaker):
    await _world(pg_sessionmaker, executions=2, cap=100)
    late = datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(text("update mandate_policies set cap_timezone='Asia/Kolkata'"))
    entry = await _reserve(pg_sessionmaker, "e0", 100, now=late)
    async with pg_sessionmaker() as s, s.begin():
        release_id = await release(s, reserve_entry_id=entry)
    async with pg_sessionmaker() as s:
        query = select(SpendLedger).where(SpendLedger.entry_id == release_id)
        row = (await s.execute(query)).scalar_one()
        assert (row.kind, row.business_date, row.reserve_entry_id) == (
            "RELEASE",
            date(2026, 11, 6),
            entry,
        )
        assert row.amount_cents == 100 and row.execution_id == "e0"
        assert await committed_cents(s, POLICY_ID, date(2026, 11, 6)) == 0
    await _reserve(pg_sessionmaker, "e1", 100, now=late)  # the freed cap is usable again


async def test_release_is_idempotent_and_only_once(pg_sessionmaker):
    await _world(pg_sessionmaker)
    entry = await _reserve(pg_sessionmaker, "e0", 100)

    async def rel():
        async with pg_sessionmaker() as s, s.begin():
            return await release(s, reserve_entry_id=entry)

    ids = await asyncio.gather(rel(), rel(), rel())
    assert len(set(ids)) == 1
    async with pg_sessionmaker() as s:
        assert await committed_cents(s, POLICY_ID, date(2026, 11, 5)) == 0


async def test_release_of_unknown_or_release_entry_fails(pg_sessionmaker):
    await _world(pg_sessionmaker)
    entry = await _reserve(pg_sessionmaker, "e0", 100)
    async with pg_sessionmaker() as s, s.begin():
        release_id = await release(s, reserve_entry_id=entry)
    for bad in (999_999, release_id):
        with pytest.raises(ValueError):
            async with pg_sessionmaker() as s, s.begin():
                await release(s, reserve_entry_id=bad)


async def test_no_active_mandate_fails_closed(pg_sessionmaker):
    await _world(pg_sessionmaker)
    async with pg_sessionmaker() as s, s.begin():
        await s.execute(text("update mandate_policies set is_active=false"))
    with pytest.raises(NoActiveMandate):
        await _reserve(pg_sessionmaker, "e0")


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "100"])
async def test_reserve_rejects_non_positive_or_non_int_amounts(pg_sessionmaker, bad):
    await _world(pg_sessionmaker)
    with pytest.raises(ValueError):
        await _reserve(pg_sessionmaker, "e0", bad)
