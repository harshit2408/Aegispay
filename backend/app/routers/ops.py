"""Halt controls: eligible session identity, CSRF and explicit reason."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, roles
from app.db import get_session
from app.models.core import Operator
from app.models.payables import SettlementControl
from app.ops.alerts import enqueue_alert

router = APIRouter(prefix="/api/ops", tags=["ops"])


class HaltRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    halted: bool = Field(strict=True)
    reason: str = Field(min_length=10, max_length=200)


@router.post("/settlement-halt")
async def set_halt(
    body: HaltRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(roles("cfo", "admin"))],
):
    row = await session.get(SettlementControl, True, with_for_update=True)
    if row is None:
        raise HTTPException(503, "settlement controls unavailable")
    row.halted, row.halted_reason = body.halted, body.reason
    row.changed_by, row.changed_at = actor.operator_id, datetime.now(UTC)
    await enqueue_alert(session, "HALT")
    await session.commit()
    return {"halted": row.halted}


@router.get("/settlement-halt")
async def halt_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(require_operator)],
):
    row = await session.get(SettlementControl, True)
    return {"halted": row is None or row.halted}
