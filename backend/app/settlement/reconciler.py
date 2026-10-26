"""Payout reconciler: item-status state machine (PRD §9.3) with a 15 s poll for up to 30 min (R6).

Webhooks are only hints (R10): every money transition here is decided from a GET of the payout
item. `reconcile(execution_id)` is the single entry used by both the poller and the webhook
consumer. Item SUCCESS records the supplier-side invoice payment once (R7) and ends in SETTLED.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.payables import InvoiceExecution, Payout
from app.paypal.client import PayPalError
from app.paypal.payouts import PayoutCommand, PayoutItem, Payouts
from app.settlement import halt as halt_flag
from app.settlement import spend_ledger, transitions
from app.settlement.service import _lock, _payout, _set_outbox
from app.vendor_sim.gateway import VendorGateway

log = logging.getLogger("aegis.settlement")

POLL_S = 15.0
POLL_WINDOW = timedelta(minutes=30)
LOCK_KEY = 0x41454749  # "AEGI": one reconciler per database (R22)
KEEP_POLLING = frozenset({"PENDING", "ONHOLD"})
FAILED = frozenset({"FAILED", "RETURNED", "BLOCKED", "DENIED"})
REVERSED = frozenset({"REFUNDED", "REVERSED"})
POLLED = ("PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED")


class Reconciler:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        payouts: Payouts,
        vendor: VendorGateway,
        *,
        window: timedelta = POLL_WINDOW,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sm, self._payouts, self._vendor = sessionmaker, payouts, vendor
        self._window, self._clock = window, clock

    async def run_once(self) -> int:
        """Reconcile every in-flight execution; one failure never blocks the others."""
        async with self._sm() as s:
            query = (
                select(InvoiceExecution.execution_id)
                .where(InvoiceExecution.status.in_(POLLED))
                .order_by(InvoiceExecution.updated_at)
            )
            ids = list((await s.execute(query)).scalars())
        for execution_id in ids:
            try:
                await self.reconcile(execution_id)
            except Exception:  # noqa: BLE001 - retried on the next tick
                log.exception("reconcile failed execution_id=%s", execution_id)
        return len(ids)

    async def reconcile(self, execution_id: str) -> str:
        """Confirm the payout item by GET and apply the PRD §9.3 table. Returns the new status."""
        async with self._sm() as s:
            execution = await s.get(InvoiceExecution, execution_id)
            payout = await _payout(s, execution_id)
        if execution is None or payout is None:
            raise LookupError(execution_id)
        if execution.status == "PAYOUT_SUCCEEDED":
            return await self._settle(execution_id)
        if execution.status not in ("PAYOUT_SUBMITTED", "SETTLED"):
            return execution.status
        item = await self._item(payout)
        command = PayoutCommand(execution_id, payout.receiver, payout.amount_cents)
        if not item.matches(command):
            return await self._manual_review(execution_id, "ITEM_MISMATCH", halt=True)
        status = item.status or ""
        if status == "SUCCESS":
            return await self._succeeded(execution_id)
        if execution.status == "SETTLED" and status not in REVERSED:
            return "SETTLED"
        if status in KEEP_POLLING:
            return await self._still_pending(execution_id, status)
        if status == "UNCLAIMED":
            return await self._move(execution_id, "FROZEN", status, "STALE_PAYEE", release=False)
        if status in FAILED:
            return await self._move(execution_id, "PAYOUT_FAILED", status, status, release=True)
        if status in REVERSED:
            log.error("ALERT payout %s after success execution_id=%s", status, execution_id)
            return await self._move(
                execution_id, "NEEDS_MANUAL_REVIEW", status, status, release=True
            )
        log.warning("unknown item status %r execution_id=%s", status, execution_id)
        return execution.status

    async def _item(self, payout: Payout) -> PayoutItem:
        item_id = payout.payout_item_id
        if item_id is None:
            batch = await self._payouts.get_batch(
                payout.payout_batch_id, execution_id=payout.execution_id
            )
            item_id = batch.items[0].payout_item_id
        return await self._payouts.get_item(item_id, execution_id=payout.execution_id)

    async def _still_pending(self, execution_id: str, status: str) -> str:
        async with self._sm() as s, s.begin():
            execution = await _lock(s, execution_id)
            payout = await _payout(s, execution_id)
            payout.item_status, payout.last_polled_at = status, func.now()
            expired = self._clock() - execution.updated_at > self._window
        if expired:
            return await self._manual_review(execution_id, "POLL_WINDOW_EXPIRED")
        return "PAYOUT_SUBMITTED"

    async def _move(
        self, execution_id: str, to: str, item_status: str, reason: str, *, release: bool
    ) -> str:
        async with self._sm() as s, s.begin():
            execution = await _lock(s, execution_id)
            payout = await _payout(s, execution_id)
            payout.item_status, payout.last_polled_at = item_status, func.now()
            if not await transitions.apply_or_ignore(s, execution, to):
                return execution.status
            if release:
                await _release_open_reserve(s, execution_id)
            await _set_outbox(s, execution_id, "FAILED", reason)
            return to

    async def _succeeded(self, execution_id: str) -> str:
        async with self._sm() as s, s.begin():
            execution = await _lock(s, execution_id)
            payout = await _payout(s, execution_id)
            payout.item_status, payout.last_polled_at = "SUCCESS", func.now()
            if execution.status == "SETTLED":
                return "SETTLED"
            await transitions.apply(s, execution, "PAYOUT_SUCCEEDED")
        return await self._settle(execution_id)

    async def _settle(self, execution_id: str) -> str:
        """Record the invoice payment once (R7), then SETTLED. Safe to re-enter after a crash."""
        async with self._sm() as s:
            execution = await s.get(InvoiceExecution, execution_id)
            payout = await _payout(s, execution_id)
        invoice_id = execution.paypal_invoice_id
        invoice = await self._vendor.get_invoice(invoice_id, execution_id=execution_id)
        already = invoice.is_fully_paid or (invoice.paid_cents or 0) >= execution.approved_cents
        if not already:
            try:
                await self._vendor.record_payment(
                    invoice_id,
                    execution_id=execution_id,
                    amount_cents=execution.approved_cents,
                    payment_date=self._clock().date(),
                    note=(
                        f"AegisPay payout batch {payout.payout_batch_id}"
                        f" item {payout.payout_item_id}"
                    ),
                )
            except PayPalError:
                log.exception("record payment failed execution_id=%s", execution_id)
                return await self._manual_review(execution_id, "RECORD_PAYMENT_FAILED")
        async with self._sm() as s, s.begin():
            execution = await _lock(s, execution_id)
            await transitions.apply(s, execution, "SETTLED")
            if execution.route == "SHORT_PAY":
                execution.short_pay_balance = "NOTICE_SENT"
        return "SETTLED"

    async def _manual_review(self, execution_id: str, reason: str, *, halt: bool = False) -> str:
        """Reservation is kept: money may have moved (R6, R8)."""
        async with self._sm() as s, s.begin():
            execution = await _lock(s, execution_id)
            if not await transitions.apply_or_ignore(s, execution, "NEEDS_MANUAL_REVIEW"):
                return execution.status
            await _set_outbox(s, execution_id, "FAILED", reason)
            if halt:
                await halt_flag.halt(s, f"{reason} execution_id={execution_id}")
        log.error("NEEDS_MANUAL_REVIEW %s execution_id=%s", reason, execution_id)
        return "NEEDS_MANUAL_REVIEW"


async def _release_open_reserve(session: AsyncSession, execution_id: str) -> None:
    reserve = await spend_ledger.open_reserve(session, execution_id)
    if reserve is None:
        log.warning("no open reservation to release execution_id=%s", execution_id)
        return
    await spend_ledger.release(session, reserve_entry_id=reserve.entry_id)


async def run_forever(
    reconciler: Reconciler,
    sessionmaker: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    poll_s: float = POLL_S,
) -> None:
    """Poll until `stop`. A session-level advisory lock keeps a deploy overlap from running two."""
    async with sessionmaker() as lock_session:
        query = select(func.pg_try_advisory_lock(LOCK_KEY))
        if not (await lock_session.execute(query)).scalar():
            log.warning("another reconciler holds the advisory lock; exiting")
            return
        while not stop.is_set():
            await reconciler.run_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            except TimeoutError:
                pass
