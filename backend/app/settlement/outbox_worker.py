"""Outbox worker: leased SKIP LOCKED claims, 2 s poll, halt checked each tick (PRD §11.4, R19)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.payables import InvoiceExecution, SettlementOutbox
from app.settlement import transitions
from app.settlement.halt import is_halted
from app.settlement.service import SettlementService

log = logging.getLogger("aegis.settlement")

LEASE = timedelta(seconds=60)
POLL_S = 2.0
MAX_ATTEMPTS = 5


async def claim_next(session: AsyncSession, now: datetime) -> SettlementOutbox | None:
    """Claim one PENDING (or lease-expired CLAIMED) command; workers never share a row."""
    ready = or_(
        SettlementOutbox.status == "PENDING",
        and_(
            SettlementOutbox.status == "CLAIMED",
            or_(SettlementOutbox.claimed_until <= now, SettlementOutbox.claimed_until.is_(None)),
        ),
    )
    query = (
        select(SettlementOutbox)
        .where(ready)
        .order_by(SettlementOutbox.created_at, SettlementOutbox.command_id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    while (row := (await session.execute(query)).scalar_one_or_none()) is not None:
        if row.attempts < MAX_ATTEMPTS:
            row.status = "CLAIMED"
            row.claimed_until = now + LEASE
            row.attempts += 1
            return row
        row.status, row.claimed_until = "FAILED", None
        row.last_error = row.last_error or "MAX_ATTEMPTS"
        log.error("outbox command gave up execution_id=%s", row.execution_id)
        stuck = select(InvoiceExecution).where(
            InvoiceExecution.execution_id == row.execution_id,
            InvoiceExecution.status.in_(("PAYOUT_SENDING", "APPROVED_FOR_PAYMENT")),
        )
        execution = (await session.execute(stuck.with_for_update())).scalar_one_or_none()
        if execution is not None:
            target = "NEEDS_MANUAL_REVIEW" if execution.status == "PAYOUT_SENDING" else "FROZEN"
            await transitions.apply(session, execution, target)
        await session.flush()
    return None


async def tick(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: SettlementService,
    now: datetime | None = None,
) -> bool:
    """One worker pass; True if a command was handled (so the caller can drain without sleeping)."""
    async with sessionmaker() as s:
        if await is_halted(s):
            return False
    async with sessionmaker() as s, s.begin():
        row = await claim_next(s, now or datetime.now(UTC))
        execution_id = row.execution_id if row else None
    if execution_id is None:
        return False
    try:
        await service.execute(execution_id)
    except Exception as err:  # noqa: BLE001 - keep the worker alive; lease expiry retries it
        log.exception("settlement failed execution_id=%s", execution_id)
        async with sessionmaker() as s, s.begin():
            query = select(SettlementOutbox).where(SettlementOutbox.execution_id == execution_id)
            (await s.execute(query)).scalar_one().last_error = type(err).__name__
    return True


async def run_forever(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: SettlementService,
    stop: asyncio.Event,
    poll_s: float = POLL_S,
) -> None:
    while not stop.is_set():
        if await tick(sessionmaker, service):
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_s)
        except TimeoutError:
            pass
