"""GET /api/health (PRD §13, R12): public, booleans and ages only, never identifiers or errors.

HTTP 503 only when the database is unreachable. Every other gap is HTTP 200 "degraded", so an
uptime ping or a platform health check does not recycle a healthy API because PayPal or the
settlement process is slow. Treasury-side facts are read from settlement's own report (R22).
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.payables import HEALTH_CHECKS, AuditAnchor, ServiceHealth, SettlementControl

logger = logging.getLogger(__name__)
HEARTBEAT_MAX_AGE_SECONDS = 90


async def _read(session: AsyncSession, statement) -> Any:
    """A missing table or role grant must not take the endpoint down; it reads as unknown."""
    try:
        return (await session.execute(statement)).first()
    except Exception as error:
        logger.warning("health: optional probe unavailable error=%s", type(error).__name__)
        await session.rollback()
        return None


def _age(moment: datetime | None) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - moment).total_seconds()))


async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health: database check failed")
        return JSONResponse(status_code=503, content={"status": "error", "db": False})
    settings = get_settings()
    beat = await _read(
        session,
        select(ServiceHealth.checked_at, ServiceHealth.details).where(
            ServiceHealth.service == "settlement"
        ),
    )
    beat_age = _age(beat.checked_at) if beat else None
    alive = beat_age is not None and beat_age <= HEARTBEAT_MAX_AGE_SECONDS
    details = beat.details if beat and alive and isinstance(beat.details, dict) else {}
    checks = {
        name: details[name] if type(details.get(name)) is bool else None for name in HEALTH_CHECKS
    }
    control = await _read(session, select(SettlementControl.halted))
    # Fail closed like /api/ops/status: missing controls read as halted.
    halted = True if control is None else bool(control.halted)
    anchor = await _read(session, select(func.max(AuditAnchor.created_at)))
    anchor_age = _age(anchor[0]) if anchor else None
    healthy = alive and all(value is True for value in checks.values()) and halted is False
    return JSONResponse(
        content={
            "status": "ok" if healthy else "degraded",
            "db": True,
            "demo_enabled": settings.demo_enabled,
            "replay_mode": settings.demo_replay_mode,
            "settlement_alive": alive,
            "settlement_report_age_seconds": beat_age,
            "checks": checks,
            "halted": halted,
            "last_anchor_age_seconds": anchor_age,
        }
    )
