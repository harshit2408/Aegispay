"""Authenticated PDF admission and exact duplicate blocking (R9, R22, R25)."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.events import enqueue_event
from app.auth import COOKIE, roles, token_hash, utc
from app.config import get_settings
from app.db import get_session
from app.decision.match import normalize_invoice_number
from app.demo.runtime import live_detector
from app.models.auth import OperatorSession
from app.models.core import MandatePolicy, Operator, PurchaseOrder, VendorMaster
from app.models.payables import InvoiceDocument, InvoiceExecution
from app.paypal.client import PayPalAuthError, PayPalError

router = APIRouter(prefix="/api/documents", tags=["documents"])
MAX_PDF_BYTES = 10 * 1024 * 1024
Session = Annotated[AsyncSession, Depends(get_session)]
Reference = Annotated[str, Form(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class DocumentReceipt(BaseModel):
    execution_id: str
    duplicate_of_execution_id: str | None


async def _duplicate(session, invoice_id, vendor_id, normalized=None):
    same = [InvoiceExecution.paypal_invoice_id == invoice_id]
    if normalized:
        same.append(
            (InvoiceExecution.vendor_id == vendor_id)
            & (InvoiceExecution.invoice_number_norm == normalized)
        )
    return await session.scalar(
        select(InvoiceExecution.execution_id)
        .where(InvoiceExecution.status.not_in(("REJECTED", "DUPLICATE_BLOCKED")), or_(*same))
        .order_by(InvoiceExecution.created_at, InvoiceExecution.execution_id)
        .limit(1)
    )


@router.post("", status_code=202, response_model=DocumentReceipt)
async def upload_document(
    request: Request,
    background: BackgroundTasks,
    session: Session,
    actor: Annotated[Operator, Depends(roles("controller", "cfo"))],
    file: Annotated[UploadFile, File()],
    po_id: Reference,
    paypal_invoice_id: Reference,
):
    require_live_admission(request)
    pdf = await file.read(MAX_PDF_BYTES + 1)
    if len(pdf) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF exceeds 10 MiB")
    if not pdf.startswith(b"%PDF-"):
        raise HTTPException(422, "A PDF is required")
    return await admit_pdf(
        request, background, session, actor.operator_id, pdf, po_id, paypal_invoice_id
    )


def require_live_admission(request: Request) -> None:
    dispatcher = getattr(request.app.state, "dispatcher", None)
    reader = getattr(request.app.state, "vendor_reader", None)
    if dispatcher is None or reader is None or get_settings().demo_replay_mode:
        raise HTTPException(503, "Live document admission is unavailable")


async def admit_pdf(
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession,
    actor_id: str,
    pdf: bytes,
    po_id: str,
    paypal_invoice_id: str,
    *,
    source: str = "PDF_UPLOAD",
    note: dict | None = None,
) -> DocumentReceipt:
    """The one live admission path: /api/documents and the live demo toolbar (PRD §13)."""
    settings = get_settings()
    reader = request.app.state.vendor_reader
    # Count attempts against the authenticated session before external work.
    auth = await session.get(
        OperatorSession, token_hash(request.cookies[COOKIE]), with_for_update=True
    )
    if auth is None:
        raise HTTPException(401, "Session expired")
    now = datetime.now(UTC)
    if (
        auth.upload_window_started is None
        or utc(auth.upload_window_started) + timedelta(minutes=1) <= now
    ):
        auth.upload_window_started = now
        auth.upload_count = 0
    if auth.upload_count >= 10:
        raise HTTPException(429, "Document admission throttled")
    auth.upload_count += 1
    await session.commit()
    po = await session.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(404, "Purchase order unavailable")
    vendor_id = po.vendor_id
    vendor = await session.get(VendorMaster, vendor_id)
    invoice_email = vendor.verified_paypal_email
    mandate = await session.scalar(select(MandatePolicy).where(MandatePolicy.is_active))
    if mandate is None:
        raise HTTPException(409, "No active mandate")
    # R24: the kill-switch toggle exists only in demo deployments; otherwise always ON.
    detector = await live_detector(session) if settings.demo_enabled else True
    original = await _duplicate(session, paypal_invoice_id, vendor_id)
    normalized = invoice_number = None
    await session.commit()
    if original is None:
        try:
            invoice = await reader.get_invoice(paypal_invoice_id)
        except (PayPalError, PayPalAuthError):
            raise HTTPException(503, "Unable to verify PayPal invoice") from None
        if (
            invoice.invoice_id != paypal_invoice_id
            or invoice.reference != po_id
            or (invoice.invoicer_email or "").casefold() != invoice_email.casefold()
        ):
            raise HTTPException(409, "PayPal invoice does not match this purchase order")
        invoice_number = invoice.invoice_number
        normalized = normalize_invoice_number(invoice_number)
        original = await _duplicate(session, paypal_invoice_id, vendor_id, normalized)

    execution_id = str(uuid4())
    storage = Path(settings.storage_path) / (execution_id + ".pdf")

    def save():
        storage.parent.mkdir(parents=True, exist_ok=True)
        with storage.open("xb") as output:
            output.write(pdf)

    try:
        await asyncio.to_thread(save)
        session.add(
            InvoiceDocument(
                document_id=execution_id,
                source=source,
                sha256=hashlib.sha256(pdf).hexdigest(),
                storage_path=str(storage.resolve()),
                uploaded_by=actor_id,
            )
        )
        await session.flush()

        def new_case(duplicate):
            return InvoiceExecution(
                execution_id=execution_id,
                document_id=execution_id,
                langgraph_thread_id=execution_id,
                po_id=po_id,
                vendor_id=vendor_id,
                paypal_invoice_id=paypal_invoice_id,
                vendor_invoice_number=invoice_number,
                invoice_number_norm=normalized,
                policy_id=mandate.policy_id,
                policy_version=mandate.version,
                detector_enabled=detector,
                status="DUPLICATE_BLOCKED" if duplicate else "RECEIVED",
                route="DUPLICATE_BLOCKED" if duplicate else None,
                match_report_json={"duplicate_of": duplicate} if duplicate else None,
            )

        try:
            async with session.begin_nested():
                session.add(new_case(original))
                await session.flush()
        except IntegrityError:
            original = await _duplicate(session, paypal_invoice_id, vendor_id, normalized)
            if original is None:
                raise
            session.add(new_case(original))
            await session.flush()
        await enqueue_event(
            session,
            execution_id=execution_id,
            actor=actor_id,
            event_type="DUPLICATE_BLOCKED" if original else "DOCUMENT_RECEIVED",
            payload={**(note or {}), **({"duplicate_of": original} if original else {})},
        )
        await session.commit()
    except Exception:
        # A failed commit can have an unknown outcome. Keep the immutable PDF for recovery.
        await session.rollback()
        raise
    if original is None:
        background.add_task(request.app.state.run_case, execution_id)
    return DocumentReceipt(execution_id=execution_id, duplicate_of_execution_id=original)
