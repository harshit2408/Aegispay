"""Settlement-side consumer of stored webhooks (PRD §9.4, R10).

A verified webhook is only a hint: each unprocessed Treasury event triggers a GET-driven
reconcile of its execution, and nothing in the event body is trusted for money. Simulated and
unverified events are never selected. Vendor events carry no money transition; they are marked
processed.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.payables import InvoiceExecution, Payout, WebhookEvent
from app.settlement.reconciler import Reconciler

log = logging.getLogger("aegis.settlement")

BATCH_SIZE = 50
CONFIRMABLE = ("PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED", "SETTLED")


class WebhookConsumer:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], reconciler: Reconciler):
        self._sm, self._reconciler = sessionmaker, reconciler

    async def run_once(self) -> int:
        async with self._sm() as s:
            query = (
                select(WebhookEvent)
                .where(
                    WebhookEvent.processed_at.is_(None),
                    WebhookEvent.signature_verified,
                    ~WebhookEvent.simulated,
                )
                .order_by(WebhookEvent.received_at)
                .limit(BATCH_SIZE)
            )
            events = list((await s.execute(query)).scalars())
        for event in events:
            try:
                confirmed = await self._handle(event)
            except Exception:  # noqa: BLE001 - left unprocessed, retried on the next tick
                log.exception("webhook event failed event_id=%s", event.event_id)
                continue
            await self._mark(event.event_id, confirmed)
        return len(events)

    async def _handle(self, event: WebhookEvent) -> bool:
        if event.app != "TREASURY":
            return False
        async with self._sm() as s:
            ours = Payout.payout_item_id == event.resource_id
            ours = or_(ours, Payout.payout_batch_id == event.resource_id)
            execution_id = (await s.execute(select(Payout.execution_id).where(ours))).scalar()
            if execution_id is None:
                return False
            before = (await s.get(InvoiceExecution, execution_id)).status
        await self._reconciler.reconcile(execution_id)
        return before in CONFIRMABLE

    async def _mark(self, event_id: str, confirmed: bool) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                update(WebhookEvent)
                .where(WebhookEvent.event_id == event_id, WebhookEvent.processed_at.is_(None))
                .values(processed_at=datetime.now(UTC), confirmed_by_get=confirmed)
            )
