"""Vendor Simulator supplier view and notice response (PRD §13, §14.4).

Supplier-facing: invoice, PayPal status, money received and sent notices only. Never risk score,
gate reasons, thresholds, mandate values or route (R14). Role is "any" by PRD §13: this is a
simulator with no real supplier login, so any authenticated operator can act as the supplier.
Each response stays attributable: `responded_by` comes from the session (R11) and an audit event
is queued.
"""

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_csrf, require_operator
from app.config import get_settings
from app.db import get_session
from app.demo.runtime import archive_filter
from app.models.core import Operator, VendorMaster
from app.models.payables import InvoiceExecution, Payout, ShortPayNotice
from app.routers.contracts import INVOICE_LIVE_STATUSES
from app.schemas.case import APIError
from app.schemas.decision import Cents
from app.vendor_sim.notices import NoticeConflict, respond

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vendor-sim", tags=["vendor-sim"])
Session = Annotated[AsyncSession, Depends(get_session)]
MAX_ROWS = 50
LIVE_STATUS_CONCURRENCY = 5
LIVE_STATUS_TIMEOUT_S = 3.0
LIVE_STATUS_TOTAL_S = 8.0
ROLE_NOTE = (
    "Role: any authenticated operator (PRD §13). Simulator only: the operator acts as the "
    "supplier; responses are attributable via responded_by and the audit chain."
)
ERRORS = {401: {"model": APIError, "description": "No valid session"}}


class SupplierNotice(BaseModel):
    notice_id: str
    text: str = Field(description="Untrusted LLM prose with template-filled numbers; plain text")
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
    received_cents: Cents = Field(description="Payout item confirmed SUCCESS by PayPal, else 0")
    notice: SupplierNotice | None = Field(description="Present once settlement sent the notice")


class NoticeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["ACCEPT", "CONTEST"]
    # No control characters (incl. NUL, newline): one plain line, safe for Postgres and logs.
    reason: str | None = Field(default=None, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")


class NoticeReceipt(BaseModel):
    notice_id: str
    short_pay_balance: Literal["ACCEPTED", "CONTESTED"]
    responded_at: datetime


def _stored_status(row) -> str | None:
    return (row.match_report_json or {}).get("paypal_invoice_status")


async def _paypal_status(reader, row, semaphore) -> str | None:
    """Live status after payout success; stored match-time status on any delay or error."""
    stored = _stored_status(row)
    if reader is None or row.status not in INVOICE_LIVE_STATUSES:
        return stored
    try:
        async with semaphore:
            invoice = await asyncio.wait_for(
                reader.get_invoice(row.paypal_invoice_id), LIVE_STATUS_TIMEOUT_S
            )
        return invoice.status
    except Exception as exc:  # read-only refresh: never fail the supplier view on PayPal
        log.warning(
            "vendor-sim status fallback execution_id=%s error=%s",
            row.execution_id,
            type(exc).__name__,
        )
        return stored


@router.get(
    "/invoices",
    response_model=list[SupplierInvoice],
    operation_id="list_vendor_sim_invoices",
    summary="Supplier view: invoices, payments received and sent short-pay notices",
    description=ROLE_NOTE + " Open notices first, then newest; at most 50 rows.",
    responses=ERRORS,
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
        .order_by(
            case(
                (
                    (InvoiceExecution.short_pay_balance == "NOTICE_SENT")
                    & ShortPayNotice.notice_id.is_not(None),
                    0,
                ),
                else_=1,
            ),
            InvoiceExecution.created_at.desc(),
            InvoiceExecution.execution_id,
        )
        .limit(MAX_ROWS)
    )
    archived = await archive_filter(session, get_settings())
    if archived is not None:
        query = query.where(~archived)
    rows = (await session.execute(query)).all()
    await session.commit()  # no DB transaction is held across the PayPal reads below
    semaphore = asyncio.Semaphore(LIVE_STATUS_CONCURRENCY)
    reader = getattr(request.app.state, "vendor_reader", None)
    tasks = [asyncio.create_task(_paypal_status(reader, r[0], semaphore)) for r in rows]
    late = set()
    if tasks:  # overall bound: rows still queued or in flight keep their stored status
        _, late = await asyncio.wait(tasks, timeout=LIVE_STATUS_TOTAL_S)
        for task in late:
            task.cancel()
    statuses = [
        _stored_status(r[0]) if task in late else task.result()
        for task, r in zip(tasks, rows, strict=True)
    ]
    return [
        SupplierInvoice(
            paypal_invoice_id=row.paypal_invoice_id,
            invoice_number=row.vendor_invoice_number,
            vendor_name=vendor_name,
            paypal_status=status,
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
            if notice is not None and row.short_pay_balance not in (None, "NONE")
            else None,
        )
        for (row, vendor_name, payout, notice), status in zip(rows, statuses, strict=True)
    ]


@router.post(
    "/notices/{notice_id}/respond",
    response_model=NoticeReceipt,
    operation_id="respond_vendor_sim_notice",
    summary="Supplier Accept / Contest of a sent short-pay notice (one response only)",
    description=ROLE_NOTE + " Requires the X-CSRF-Token header. The paid amount never changes.",
    responses={
        **ERRORS,
        403: {"model": APIError, "description": "Missing or invalid CSRF token"},
        404: {"model": APIError, "description": "Unknown notice"},
        409: {"model": APIError, "description": "Notice not sent yet, or already answered"},
    },
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
