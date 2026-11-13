"""Transactional alert outbox; settlement alone receives the optional webhook secret."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import OpsAlert

log = logging.getLogger("aegis.ops")
EVENTS = frozenset(
    {
        "HALT",
        "VENDOR_CHANGE",
        "MANDATE_ACTIVATED",
        "WEBHOOK_SIGNATURE_FAILURE",
        "APPROVAL_OUTSIDE_HOURS",
        "SHORT_PAY_NOTICE_MISSING",  # §12.4: short-paid without a stored notice
    }
)


async def enqueue_alert(session: AsyncSession, event: str, execution_id: str | None = None) -> None:
    if event not in EVENTS or (
        execution_id is not None
        and (len(execution_id) > 128 or not all(c.isalnum() or c in "-_" for c in execution_id))
    ):
        raise ValueError("invalid alert")
    session.add(
        OpsAlert(
            alert_id=str(uuid4()),
            event=event,
            execution_id=execution_id,
            created_at=datetime.now(UTC),
        )
    )
    log.warning("ops_alert event=%s execution_id=%s", event, execution_id)


async def deliver_alerts(sessions, url: str) -> None:
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(OpsAlert)
                .where(OpsAlert.delivered_at.is_(None))
                .order_by(OpsAlert.created_at)
                .limit(50)
            )
        ).all()
        pending = [(row.alert_id, row.event, row.execution_id) for row in rows]
    for alert_id, event, execution_id in pending:
        if url:
            try:
                async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                    response = await client.post(
                        url, json={"event": event, "execution_id": execution_id}
                    )
                    response.raise_for_status()
            except httpx.HTTPError:
                log.error("ops_alert_delivery_failed event=%s", event)
                break
        async with sessions() as session, session.begin():
            row = await session.get(OpsAlert, alert_id)
            if row:
                row.delivered_at = datetime.now(UTC)
