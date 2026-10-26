"""SettlementService: the only way money moves (PRD R1, R5, R19, R20, §11.4).

`execute(execution_id)` takes an execution ID and nothing else. Payee and amount come from the
frozen `settlement_outbox` row written at approval. Order of operations:

  recheck (R20) -> [tx: lock, recheck, reserve spend, insert payouts row, PAYOUT_SENDING]
  (write-ahead) -> POST payout, or adopt the batch PayPal already holds (R19) -> GET batch and
  assert it equals the command (R5) -> [tx: record ids, PAYOUT_SUBMITTED].

No DB lock is held across an HTTP call. Any unresolved outcome after the write-ahead commit goes
to NEEDS_MANUAL_REVIEW with the spend reservation KEPT, because money may already have moved.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.payables import InvoiceExecution, Payout, SettlementOutbox
from app.paypal.client import PayPalError
from app.paypal.payouts import PayoutBatch, PayoutCommand, PayoutMismatch, Payouts, PayoutUnresolved
from app.settlement import halt as halt_flag
from app.settlement import spend_ledger, transitions
from app.settlement.prechecks import db_recheck, invoice_recheck
from app.vendor_sim.gateway import VendorReader

log = logging.getLogger("aegis.settlement")

PAYOUT_RECOVERY_TTL = timedelta(minutes=30)  # PAYOUT_RECOVERY_TTL_MIN (R19)
PROGRESSED = frozenset({"PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED", "SETTLED", "PAYOUT_FAILED"})
UNRESOLVED = (PayPalError, PayoutUnresolved, httpx.TransportError)


class NoCommand(Exception):
    """No outbox command or execution for this ID: nothing to pay."""


class SettlementService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        payouts: Payouts,
        vendor_reader: VendorReader,
        *,
        recovery_ttl: timedelta = PAYOUT_RECOVERY_TTL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sm = sessionmaker
        self._payouts = payouts
        self._vendor = vendor_reader
        self._ttl = recovery_ttl
        self._clock = clock

    async def execute(self, execution_id: str) -> str:
        """Pay one approved execution. Returns the execution status it ends in. Idempotent."""
        async with self._sm() as s:
            execution = await s.get(InvoiceExecution, execution_id)
            outbox = await _outbox(s, execution_id)
            if execution is None or outbox is None:
                raise NoCommand(execution_id)
            status = execution.status
            command = PayoutCommand(
                execution_id=execution_id,
                receiver_payer_id=outbox.receiver_payer_id,
                amount_cents=outbox.amount_cents,
                note="AegisPay " + execution_id + " / " + (execution.po_id or ""),
            )
        if status == "APPROVED_FOR_PAYMENT":
            return await self._start(command)
        if status == "PAYOUT_SENDING":
            return await self._recover(command)
        log.info("execute: nothing to do status=%s execution_id=%s", status, execution_id)
        await self._close(execution_id, "DONE" if status in PROGRESSED else "FAILED", status)
        return status

    async def _start(self, command: PayoutCommand) -> str:
        async with self._sm() as s:
            execution = await s.get(InvoiceExecution, command.execution_id)
            reason = await invoice_recheck(self._vendor, execution)  # HTTP, before any lock
        if reason:
            return await self._freeze(command, reason)
        async with self._sm() as s, s.begin():
            execution = await _lock(s, command.execution_id)
            if execution.status != "APPROVED_FOR_PAYMENT":
                return execution.status  # another worker got here first
            outbox = await _outbox(s, command.execution_id)
            reason = await db_recheck(s, execution, outbox)
            if reason is None:
                try:
                    await spend_ledger.reserve(
                        s,
                        execution_id=command.execution_id,
                        policy_id=execution.policy_id,
                        vendor_id=execution.vendor_id,
                        amount_cents=command.amount_cents,
                        now=self._clock(),
                    )
                except spend_ledger.OverCap:
                    reason = "OVER_CAP"
                except spend_ledger.NoActiveMandate:
                    reason = "NO_ACTIVE_MANDATE"
            if reason:
                await transitions.apply(s, execution, "FROZEN")
                await _set_outbox(s, command.execution_id, "FAILED", reason)
                log.warning(
                    "frozen before payout: %s execution_id=%s", reason, command.execution_id
                )
                return "FROZEN"
            s.add(
                Payout(
                    payout_id="PAY-" + command.execution_id,
                    execution_id=command.execution_id,
                    sender_batch_id=command.sender_batch_id,
                    receiver=command.receiver_payer_id,
                    amount_cents=command.amount_cents,
                )
            )
            await transitions.apply(s, execution, "PAYOUT_SENDING")
        return await self._post_and_submit(command)

    async def _recover(self, command: PayoutCommand) -> str:
        """PAYOUT_SENDING after a crash or lease expiry (R19)."""
        async with self._sm() as s:
            execution = await s.get(InvoiceExecution, command.execution_id)
            payout = await _payout(s, command.execution_id)
            outbox = await _outbox(s, command.execution_id)
            if payout is not None and payout.payout_batch_id:
                return await self._post_and_submit(command, known_batch_id=payout.payout_batch_id)
            if payout is None:
                reason = "NO_WRITE_AHEAD_ROW"
            elif self._clock() - payout.created_at > self._ttl:
                reason = "RECOVERY_TTL_EXPIRED"
            else:
                reason = await db_recheck(s, execution, outbox)
        if reason is None:
            reason = await invoice_recheck(self._vendor, execution)
        if reason:
            return await self._manual_review(command, reason)
        return await self._post_and_submit(command)

    async def _post_and_submit(
        self, command: PayoutCommand, known_batch_id: str | None = None
    ) -> str:
        batch_id = known_batch_id
        if batch_id is None:  # halt can land between the write-ahead commit and the POST (R23)
            async with self._sm() as s:
                if await halt_flag.is_halted(s):
                    return await self._manual_review(command, "HALTED_BEFORE_POST")
        try:
            if batch_id is None:
                batch_id = (await self._payouts.create_or_adopt(command)).payout_batch_id
            batch = await self._payouts.get_batch(batch_id, execution_id=command.execution_id)
        except PayoutMismatch as err:
            return await self._manual_review(
                command, "PAYOUT_MISMATCH", halt=True, batch_id=err.payout_batch_id
            )
        except UNRESOLVED as err:
            log.exception("payout outcome unresolved execution_id=%s", command.execution_id)
            return await self._manual_review(
                command, "UNRESOLVED:" + type(err).__name__, batch_id=batch_id
            )
        if not batch.matches(command):
            return await self._manual_review(
                command, "RESPONSE_MISMATCH", halt=True, batch_id=batch_id
            )
        await self._submitted(command, batch)
        return "PAYOUT_SUBMITTED"

    async def _submitted(self, command: PayoutCommand, batch: PayoutBatch) -> None:
        item = batch.items[0]
        async with self._sm() as s, s.begin():
            execution = await _lock(s, command.execution_id)
            if execution.status != "PAYOUT_SENDING":
                return  # a concurrent worker (lease overlap) already submitted it
            payout = await _payout(s, command.execution_id)
            payout.payout_batch_id = batch.payout_batch_id
            payout.payout_item_id = item.payout_item_id
            payout.paypal_receiver = item.receiver
            payout.paypal_amount_cents = item.amount_cents
            payout.batch_status = batch.batch_status
            payout.item_status = item.status
            await transitions.apply(s, execution, "PAYOUT_SUBMITTED")
            await _set_outbox(s, command.execution_id, "DONE", None)

    async def _freeze(self, command: PayoutCommand, reason: str) -> str:
        async with self._sm() as s, s.begin():
            execution = await _lock(s, command.execution_id)
            await transitions.apply(s, execution, "FROZEN")
            await _set_outbox(s, command.execution_id, "FAILED", reason)
        log.warning("frozen before payout: %s execution_id=%s", reason, command.execution_id)
        return "FROZEN"

    async def _manual_review(
        self,
        command: PayoutCommand,
        reason: str,
        *,
        halt: bool = False,
        batch_id: str | None = None,
    ) -> str:
        """The spend reservation is deliberately kept: money may have moved (R8, R19)."""
        async with self._sm() as s, s.begin():
            execution = await _lock(s, command.execution_id)
            payout = await _payout(s, command.execution_id)
            if payout is not None and batch_id and payout.payout_batch_id is None:
                payout.payout_batch_id = batch_id
            if not await transitions.apply_or_ignore(s, execution, "NEEDS_MANUAL_REVIEW"):
                return execution.status
            await _set_outbox(s, command.execution_id, "FAILED", reason)
            if halt:
                await halt_flag.halt(s, reason + " execution_id=" + command.execution_id)
        log.error(
            "NEEDS_MANUAL_REVIEW %s execution_id=%s batch=%s",
            reason,
            command.execution_id,
            batch_id,
        )
        return "NEEDS_MANUAL_REVIEW"

    async def _close(self, execution_id: str, status: str, note: str) -> None:
        async with self._sm() as s, s.begin():
            await _set_outbox(s, execution_id, status, None if status == "DONE" else note)


async def _outbox(session: AsyncSession, execution_id: str) -> SettlementOutbox | None:
    query = select(SettlementOutbox).where(SettlementOutbox.execution_id == execution_id)
    return (await session.execute(query)).scalar_one_or_none()


async def _payout(session: AsyncSession, execution_id: str) -> Payout | None:
    query = select(Payout).where(Payout.execution_id == execution_id)
    return (await session.execute(query)).scalar_one_or_none()


async def _lock(session: AsyncSession, execution_id: str) -> InvoiceExecution:
    query = (
        select(InvoiceExecution)
        .where(InvoiceExecution.execution_id == execution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(query)).scalar_one()


async def _set_outbox(
    session: AsyncSession, execution_id: str, status: str, error: str | None
) -> None:
    await session.execute(
        update(SettlementOutbox)
        .where(SettlementOutbox.execution_id == execution_id)
        .values(status=status, last_error=error, claimed_until=None)
    )
