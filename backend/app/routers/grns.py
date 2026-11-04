"""Receiver-authenticated append-only receipts and corrections (R16)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth import roles
from app.db import get_session
from app.models.core import GoodsReceipt, Operator, PurchaseOrder

router = APIRouter(prefix="/api/grns", tags=["grns"])


class ReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    po_id: str = Field(min_length=1, max_length=128)
    supersedes_grn_id: str | None = Field(default=None, max_length=128)
    received_qty: int = Field(ge=0, strict=True)
    accepted_qty: int = Field(ge=0, strict=True)
    rejected_qty: int = Field(ge=0, strict=True)
    inspection_note: str | None = Field(default=None, max_length=4000)
    received_at: datetime

    @model_validator(mode="after")
    def valid_receipt(self):
        if self.accepted_qty + self.rejected_qty != self.received_qty:
            raise ValueError("receipt quantities do not balance")
        if self.received_at.tzinfo is None or self.received_at > datetime.now(UTC):
            raise ValueError("received_at must be timezone-aware and not in the future")
        return self


@router.post("", status_code=201)
async def record_receipt(
    body: ReceiptRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(roles("receiver"))],
):
    po = await session.get(PurchaseOrder, body.po_id, with_for_update=True)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    if po.created_by == actor.operator_id or po.status != "OPEN":
        raise HTTPException(409, "receipt violates PO controls")
    correction = aliased(GoodsReceipt)
    current = (
        await session.scalars(
            select(GoodsReceipt).where(
                GoodsReceipt.po_id == body.po_id,
                ~select(correction.grn_id)
                .where(correction.supersedes_grn_id == GoodsReceipt.grn_id)
                .exists(),
            )
        )
    ).all()
    if body.supersedes_grn_id and body.supersedes_grn_id not in {row.grn_id for row in current}:
        raise HTTPException(409, "correction must supersede a current same-PO receipt")
    accepted = sum(row.accepted_qty for row in current if row.grn_id != body.supersedes_grn_id)
    if accepted + body.accepted_qty > po.ordered_qty:
        raise HTTPException(409, "accepted quantity exceeds PO remaining quantity")
    row = GoodsReceipt(
        grn_id=str(uuid4()),
        **body.model_dump(),
        recorded_by=actor.operator_id,
        source="RECEIVER_SESSION",
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "receipt conflicts with receiving controls") from None
    return {"grn_id": row.grn_id, "po_id": row.po_id, "supersedes_grn_id": row.supersedes_grn_id}
