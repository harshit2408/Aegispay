"""SETTLEMENT_HALT: a single DB flag checked on every worker tick and before every payout (R23)."""

import logging

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payables import SettlementControl

log = logging.getLogger("aegis.settlement")


async def is_halted(session: AsyncSession) -> bool:
    control = await session.get(SettlementControl, True, populate_existing=True)
    return control is None or control.halted  # a missing row fails closed


async def halt(session: AsyncSession, reason: str, changed_by: str = "system") -> None:
    """Idempotent: an existing halt keeps its first reason/actor and raises no new alert."""
    changed = await session.scalar(
        update(SettlementControl)
        .where(SettlementControl.id, SettlementControl.halted.is_(False))
        .values(
            halted=True, halted_reason=reason[:200], changed_by=changed_by, changed_at=func.now()
        )
        .returning(SettlementControl.id)
    )
    log.error("SETTLEMENT_HALT reason_code=%s", reason[:200])
    if changed is None:
        return
    from app.ops.alerts import enqueue_alert

    await enqueue_alert(session, "HALT")
