"""Vendor Simulator supplier view and notice response (PRD §13, §14.4).

Supplier-facing: invoice, PayPal status, money received and sent notices only. Never risk score,
gate reasons, thresholds, mandate values or route (R14). Actor comes from the session (R11).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_csrf, require_operator
from app.config import get_settings
from app.db import get_session
from app.demo.runtime import archive_filter
from app.models.core import Operator, VendorMaster
from app.models.payables import InvoiceExecution, Payout, ShortPayNotice
from app.routers.contracts import _live_invoice_status
from app.schemas.case import APIError
from app.schemas.decision import Cents
from app.vendor_sim.notices import NoticeConflict, respond

router = APIRouter(prefix="/api/vendor-sim", tags=["vendor-sim"])
Session = Annotated[AsyncSession, Depends(get_session)]
MAX_ROWS = 50  # each settled row may cost one PayPal GET


class SupplierNotice(BaseModel):
    notice_id: str
    text: str  # A5 prose with template-filled numbers; untrusted LLM output, render as text
    delta_cents: Cents
    status: Literal["NOTICE_SENT", "ACCEPTED", "CONTESTED"]
    created_at: datetime
    responded_at: datetime | None


class SupplierInvoice(BaseModel):
    paypal_invoice_id: str
    invoice_number: str | None
    vendor_name: str | None
    paypal_status: str | None
    billed_cents: Cents | None
    received_cents: Cents  # payout item confirmed SUCCESS by PayPal, else 0
    notice: SupplierNotice | None


class NoticeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["ACCEPT", "CONTEST"]
    reason: str | None = Field(default=None, max_length=1000)


class NoticeReceipt(BaseModel):
    notice_id: str
    short_pay_balance: Literal["ACCEPTED", "CONTESTED"]
    responded_at: datetime


@router.get(
    "/invoices", response_model=list[SupplierInvoice], operation_id="list_vendor_sim_invoices"
)
async def list_invoices(
    request: Request, session: Session, viewer: Annotated[Operator, Depends(require_operator)]
):
    query = (
        select(InvoiceExecution, VendorMaster.vendor_name, Payout, ShortPayNotice)
        .outerjoin(VendorMaster, VendorMaster.vendor_id == InvoiceExecution.vendor_id)
        .outerjoin(Payout, Payout.execution_id == InvoiceExecution.execution_id)
        .outerjoin(ShortPayNotice, ShortPayNotice.execution_id == InvoiceExecution.execution_id)
        .where(InvoiceExecution.paypal_invoice_id.is_not(None))
        .order_by(InvoiceExecution.created_at.desc(), InvoiceExecution.execution_id)
        .limit(MAX_ROWS)
    )
    archived = await archive_filter(session, get_settings())
    if archived is not None:
        query = query.where(~archived)
    rows = (await session.execute(query)).all()
    result = []
    for row, vendor_name, payout, notice in rows:
        stored = (row.match_report_json or {}).get("paypal_invoice_status")
        sent = notice is not None and row.short_pay_balance not in (None, "NONE")
        result.append(
            SupplierInvoice(
                paypal_invoice_id=row.paypal_invoice_id,
                invoice_number=row.vendor_invoice_number,
                vendor_name=vendor_name,
                paypal_status=await _live_invoice_status(request, row, stored),
                billed_cents=row.billed_cents,
                received_cents=payout.amount_cents
                if payout is not None and payout.item_status == "SUCCESS"
                else 0,
                notice=SupplierNotice(
                    notice_id=notice.notice_id,
                    text=notice.text,
                    delta_cents=notice.delta_cents,
                    status=row.short_pay_balance,
                    created_at=notice.created_at,
                    responded_at=notice.responded_at,
                )
                if sent
                else None,
            )
        )
    return result


@router.post(
    "/notices/{notice_id}/respond",
    response_model=NoticeReceipt,
    operation_id="respond_vendor_sim_notice",
    responses={404: {"model": APIError}, 409: {"model": APIError}},
)
async def respond_to_notice(
    notice_id: str,
    body: NoticeResponse,
    session: Session,
    actor: Annotated[Operator, Depends(require_csrf)],
):
    if len(notice_id) > 128:
        raise HTTPException(404, "notice not found")
    try:
        receipt = await respond(session, notice_id, body.action, body.reason, actor.operator_id)
        await session.commit()
    except LookupError:
        await session.rollback()
        raise HTTPException(404, "notice not found") from None
    except NoticeConflict as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from None
    return receipt
