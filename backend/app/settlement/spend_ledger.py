"""Atomic daily spend cap (PRD R8, §11.4).

Callers pass an open transaction. `reserve` takes `SELECT … FOR UPDATE` on the active mandate row,
so concurrent reservations for one policy serialize and the lock is held until the caller commits. A
release references its reserve entry and copies its `business_date`; the DB allows one release per
reserve (uq_spend_one_release_per_reserve). Ints only: no float near money (R4).
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.core import MandatePolicy
from app.models.payables import SpendLedger


class OverCap(Exception):
    def __init__(self, committed_cents: int, amount_cents: int, cap_cents: int) -> None:
        super().__init__(f"{committed_cents} + {amount_cents} exceeds daily cap {cap_cents}")
        self.committed_cents = committed_cents
        self.amount_cents = amount_cents
        self.cap_cents = cap_cents


class NoActiveMandate(Exception):
    """No active mandate for the policy: fail closed, never reserve."""


def business_date(now: datetime, cap_timezone: str) -> date:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(cap_timezone)).date()


async def committed_cents(session: AsyncSession, policy_id: str, day: date) -> int:
    signed = case(
        (SpendLedger.kind == "RESERVE", SpendLedger.amount_cents), else_=-SpendLedger.amount_cents
    )
    query = select(func.coalesce(func.sum(signed), 0)).where(
        SpendLedger.policy_id == policy_id, SpendLedger.business_date == day
    )
    return int((await session.execute(query)).scalar_one())


async def open_reserve(session: AsyncSession, execution_id: str) -> SpendLedger | None:
    later = aliased(SpendLedger)
    query = select(SpendLedger).where(
        SpendLedger.execution_id == execution_id,
        SpendLedger.kind == "RESERVE",
        ~select(later.entry_id).where(later.reserve_entry_id == SpendLedger.entry_id).exists(),
    )
    return (await session.execute(query)).scalar_one_or_none()


async def reserve(
    session: AsyncSession,
    *,
    execution_id: str,
    policy_id: str,
    vendor_id: str,
    amount_cents: int,
    now: datetime | None = None,
) -> int:
    """Reserve `amount_cents` against today's cap; returns the ledger entry id.

    Re-entering for an execution that already holds an open reservation returns it (R19)."""
    if type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive int")
    mandate = (
        await session.execute(
            select(MandatePolicy)
            .where(MandatePolicy.policy_id == policy_id, MandatePolicy.is_active)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if mandate is None:
        raise NoActiveMandate(policy_id)
    existing = await open_reserve(session, execution_id)
    if existing is not None:
        if existing.amount_cents != amount_cents:
            raise ValueError("open reservation exists with a different amount")
        return existing.entry_id
    day = business_date(now or datetime.now(UTC), mandate.cap_timezone)
    committed = await committed_cents(session, policy_id, day)
    if committed + amount_cents > mandate.daily_cap_cents:
        raise OverCap(committed, amount_cents, mandate.daily_cap_cents)
    entry = SpendLedger(
        execution_id=execution_id,
        policy_id=policy_id,
        vendor_id=vendor_id,
        business_date=day,
        kind="RESERVE",
        amount_cents=amount_cents,
    )
    session.add(entry)
    await session.flush()
    return entry.entry_id


async def release(session: AsyncSession, *, reserve_entry_id: int) -> int:
    """Release a reservation in full on its own business_date. Idempotent; returns the release id.

    Only on confirmed non-payment (R8): FAILED, RETURNED, BLOCKED, DENIED or a confirmed cancel."""
    reserved = await session.get(SpendLedger, reserve_entry_id)
    if reserved is None or reserved.kind != "RESERVE":
        raise ValueError(f"{reserve_entry_id} is not a RESERVE entry")
    inserted = (
        await session.execute(
            pg_insert(SpendLedger)
            .values(
                execution_id=reserved.execution_id,
                policy_id=reserved.policy_id,
                vendor_id=reserved.vendor_id,
                business_date=reserved.business_date,
                kind="RELEASE",
                amount_cents=reserved.amount_cents,
                reserve_entry_id=reserve_entry_id,
            )
            .on_conflict_do_nothing(constraint="uq_spend_one_release_per_reserve")
            .returning(SpendLedger.entry_id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    existing = select(SpendLedger.entry_id).where(SpendLedger.reserve_entry_id == reserve_entry_id)
    return (await session.execute(existing)).scalar_one()
