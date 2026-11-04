"""R15 requests and independent approvals; only settlement may update vendor master."""

from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, roles
from app.config import get_settings
from app.db import get_session
from app.models.core import Operator, VendorMaster, VendorMasterChange
from app.ops.alerts import enqueue_alert

router = APIRouter(prefix="/api/vendors", tags=["vendors"])
Session = Annotated[AsyncSession, Depends(get_session)]


class VendorChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paypal_payer_id: str = Field(pattern=r"^[A-Z0-9]{13}$")
    verified_paypal_email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    reason: str = Field(min_length=10, max_length=1000)


class ChangeApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)


def change_view(row):
    return {
        key: getattr(row, key)
        for key in (
            "change_id",
            "vendor_id",
            "status",
            "requested_by",
            "approved_by",
            "approved_at",
            "effective_at",
        )
    }


@router.post("/{vendor_id}/changes", status_code=201)
async def request_change(
    vendor_id: str,
    body: VendorChangeRequest,
    session: Session,
    actor: Annotated[Operator, Depends(roles("vendor_admin"))],
):
    vendor = await session.get(VendorMaster, vendor_id)
    if vendor is None:
        raise HTTPException(404, "vendor not found")
    pending = await session.scalar(
        select(VendorMasterChange.change_id).where(
            VendorMasterChange.vendor_id == vendor_id,
            VendorMasterChange.status.in_(("REQUESTED", "COOLING")),
        )
    )
    if pending:
        raise HTTPException(409, "vendor already has a pending change")
    new = body.model_dump(exclude={"reason"})
    old = {key: getattr(vendor, key) for key in new}
    if old == new:
        raise HTTPException(409, "payee unchanged")
    old["version"] = vendor.version
    row = VendorMasterChange(
        change_id=str(uuid4()),
        vendor_id=vendor_id,
        old_values=old,
        new_values=new,
        requested_by=actor.operator_id,
        status="REQUESTED",
    )
    session.add(row)
    await enqueue_alert(session, "VENDOR_CHANGE")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "vendor already has a pending change") from None
    return change_view(row)


@router.post("/changes/{change_id}/approve")
async def approve_change(
    change_id: str,
    body: ChangeApproval,
    session: Session,
    actor: Annotated[Operator, Depends(roles("vendor_admin", "cfo"))],
):
    row = await session.get(VendorMasterChange, change_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "change not found")
    if row.status != "REQUESTED" or row.requested_by == actor.operator_id:
        raise HTTPException(409, "change requires an independent approver")
    now = await session.scalar(select(func.now()))
    row.approved_by, row.approved_at = actor.operator_id, now
    row.effective_at, row.status = (
        now + timedelta(hours=get_settings().payee_cooling_hours),
        "COOLING",
    )
    await enqueue_alert(session, "VENDOR_CHANGE")
    await session.commit()
    return change_view(row)


@router.get("/{vendor_id}/changes")
async def changes(
    vendor_id: str, session: Session, actor: Annotated[Operator, Depends(require_operator)]
):
    return [
        change_view(row)
        for row in (
            await session.scalars(
                select(VendorMasterChange)
                .where(VendorMasterChange.vendor_id == vendor_id)
                .order_by(VendorMasterChange.created_at)
            )
        ).all()
    ]
