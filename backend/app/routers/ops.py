"""Halt controls: eligible session identity, CSRF and explicit reason."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, roles
from app.db import get_session
from app.models.auth import OpsAlert
from app.models.core import Operator, VendorMasterChange
from app.models.payables import AuditAnchor, SettlementControl
from app.ops.alerts import enqueue_alert

router = APIRouter(prefix="/api/ops", tags=["ops"])


class HaltView(BaseModel):
    halted: bool


class AlertView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    alert_id: str
    event: str
    execution_id: str | None
    created_at: datetime
    delivered_at: datetime | None


class PendingChangeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    change_id: str
    vendor_id: str
    status: str
    requested_by: str
    approved_by: str | None
    effective_at: datetime | None


class OpsStatusView(BaseModel):
    halted: bool
    halted_reason: str | None
    velocity_breaker_tripped: bool
    alerts: list[AlertView]
    vendor_changes: list[PendingChangeView]
    last_anchor_at: datetime | None


@router.get("/status", response_model=OpsStatusView)
async def status(
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(require_operator)],
):
    if actor.role not in ("cfo", "admin"):
        raise HTTPException(403, "role not permitted")
    control = await session.get(SettlementControl, True)
    alerts = (
        await session.scalars(
            select(OpsAlert)
            .order_by(OpsAlert.created_at.desc(), OpsAlert.alert_id.desc())
            .limit(50)
        )
    ).all()
    changes = (
        await session.scalars(
            select(VendorMasterChange)
            .where(VendorMasterChange.status.in_(("REQUESTED", "COOLING")))
            .order_by(VendorMasterChange.created_at.desc())
            .limit(100)
        )
    ).all()
    anchor_at = await session.scalar(
        select(AuditAnchor.created_at).order_by(AuditAnchor.anchor_id.desc()).limit(1)
    )
    return OpsStatusView(
        halted=control is None or control.halted,
        halted_reason=control.halted_reason if control else "controls unavailable",
        velocity_breaker_tripped=bool(
            control and control.halted and control.halted_reason == "VELOCITY_BREAKER"
        ),
        alerts=[AlertView.model_validate(row) for row in alerts],
        vendor_changes=[PendingChangeView.model_validate(row) for row in changes],
        last_anchor_at=anchor_at,
    )


class HaltRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    halted: bool = Field(strict=True)
    reason: str = Field(min_length=10, max_length=200)


@router.post("/settlement-halt", response_model=HaltView)
async def set_halt(
    body: HaltRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(roles("cfo", "admin"))],
):
    if not body.halted and actor.role != "cfo":
        # R18: admin may halt in an emergency, but only the CFO resumes payouts.
        raise HTTPException(403, "only cfo can resume settlement")
    row = await session.get(SettlementControl, True, with_for_update=True)
    if row is None:
        raise HTTPException(503, "settlement controls unavailable")
    row.halted, row.halted_reason = body.halted, body.reason
    row.changed_by, row.changed_at = actor.operator_id, datetime.now(UTC)
    await enqueue_alert(session, "HALT")
    await session.commit()
    return {"halted": row.halted}


@router.get("/settlement-halt", response_model=HaltView)
async def halt_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(require_operator)],
):
    row = await session.get(SettlementControl, True)
    return {"halted": row is None or row.halted}
