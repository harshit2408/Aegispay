"""Authenticated ledger, case evidence and decision dispatch (PRD §13)."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, roles
from app.config import get_settings
from app.db import get_session
from app.decision.dispatch import DecisionConflict, DispatchUnavailable
from app.demo.runtime import archive_filter
from app.models.core import GoodsReceipt, MandatePolicy, Operator, PurchaseOrder, VendorMaster
from app.models.payables import (
    Approval,
    AuditAnchor,
    ExecutionEvent,
    InvoiceDocument,
    InvoiceExecution,
    Payout,
    POLedger,
)
from app.paypal.client import PayPalAuthError, PayPalError
from app.schemas.case import APIError, CaseDetail, DecisionReceipt, DecisionRequest, ExecutionRow

router = APIRouter(prefix="/api/executions", tags=["executions"])
Session = Annotated[AsyncSession, Depends(get_session)]
Viewer = Annotated[Operator, Depends(require_operator)]
SETTLEMENT_STEPS = (
    "PAYOUT_SENDING",
    "PAYOUT_SUBMITTED",
    "PAYOUT_SUCCEEDED",
    "PAYOUT_FAILED",
    "SETTLED",
    "NEEDS_MANUAL_REVIEW",
)
INVOICE_LIVE_STATUSES = ("PAYOUT_SUCCEEDED", "SETTLED")


async def _settlement_view(session, execution_id: str) -> tuple[list[dict], dict]:
    """Timeline from the signed event chain; anchor only if a receipt covers this case's tail.

    `verified` means PayPal said so on a GET (batch id returned, item SUCCESS), never "an event
    exists". Simulated webhook events cannot move money (R10), so no timeline step is simulated.
    The API holds no HMAC key (R22): it reports the settlement worker's anchor receipt, not a
    chain check of its own.
    """
    events = list(
        await session.scalars(
            select(ExecutionEvent)
            .where(
                ExecutionEvent.execution_id == execution_id,
                ExecutionEvent.event_type.in_(SETTLEMENT_STEPS),
            )
            .order_by(ExecutionEvent.event_seq)
        )
    )
    payout = await session.scalar(select(Payout).where(Payout.execution_id == execution_id))
    confirmed = {
        "PAYOUT_SUBMITTED": payout is not None and payout.payout_batch_id is not None,
        "PAYOUT_SUCCEEDED": payout is not None and payout.item_status == "SUCCESS",
        "SETTLED": payout is not None and payout.item_status == "SUCCESS",
    }
    timeline = [
        {
            "status": event.event_type,
            "occurred_at": event.ts.isoformat(),
            "paypal_id": payout.payout_batch_id
            if payout and event.event_type != "PAYOUT_SENDING"
            else None,
            "verified": confirmed.get(event.event_type, False),
            "simulated": False,
        }
        for event in events
    ]
    last_seq = await session.scalar(
        select(func.max(ExecutionEvent.event_seq)).where(
            ExecutionEvent.execution_id == execution_id
        )
    )
    anchor = None
    if last_seq is not None:
        anchor = await session.scalar(
            select(AuditAnchor)
            .where(AuditAnchor.event_seq >= last_seq)
            .order_by(AuditAnchor.anchor_id)
            .limit(1)
        )
    return timeline, {
        "verified": anchor is not None,
        "anchored_at": anchor.created_at.isoformat() if anchor else None,
        "head_hmac": anchor.head_hash if anchor else None,
    }


async def _live_invoice_status(request: Request, row, stored: str | None) -> str | None:
    """After the payout succeeded, ask PayPal (R7 outcome); otherwise keep the match-time status."""
    reader = getattr(request.app.state, "vendor_reader", None)
    if reader is None or row.status not in INVOICE_LIVE_STATUSES or not row.paypal_invoice_id:
        return stored
    try:
        return (await reader.get_invoice(row.paypal_invoice_id)).status
    except (PayPalError, PayPalAuthError, httpx.HTTPError):
        # A read-only status refresh must not turn a PayPal outage into a 500 case view.
        return stored


@router.get("", response_model=list[ExecutionRow], operation_id="list_executions")
async def list_executions(session: Session, viewer: Viewer):
    query = (
        select(InvoiceExecution, VendorMaster.vendor_name)
        .outerjoin(VendorMaster, VendorMaster.vendor_id == InvoiceExecution.vendor_id)
        .order_by(InvoiceExecution.created_at.desc(), InvoiceExecution.execution_id)
        .limit(200)
    )
    archived = await archive_filter(session, get_settings())
    if archived is not None:
        query = query.where(~archived)
    result = await session.execute(query)
    return [
        ExecutionRow(
            execution_id=row.execution_id,
            status=row.status,
            vendor_name=name,
            billed_cents=row.billed_cents,
            payable_cents=row.approved_cents,
            risk_score=row.risk_score,
            replayed=row.replayed,
        )
        for row, name in result
    ]


@router.get("/{execution_id}", response_model=CaseDetail, operation_id="get_execution")
async def get_execution(execution_id: str, request: Request, session: Session, viewer: Viewer):
    row = await session.get(InvoiceExecution, execution_id)
    if row is None:
        raise HTTPException(404, "Case unavailable")
    doc = await session.get(InvoiceDocument, row.document_id)
    policy = await session.get(MandatePolicy, (row.policy_id, row.policy_version))
    vendor = await session.get(VendorMaster, row.vendor_id) if row.vendor_id else None
    po = await session.get(PurchaseOrder, row.po_id) if row.po_id else None
    grn = await session.get(GoodsReceipt, row.grn_id) if row.grn_id else None
    approvals = (
        await session.execute(
            select(Approval, Operator)
            .join(Operator, Operator.operator_id == Approval.actor)
            .where(Approval.execution_id == execution_id)
            .order_by(Approval.created_at, Approval.approval_id)
        )
    ).all()
    match = row.match_report_json
    duplicate = (match or {}).get("duplicate_of")
    if match and "po_id" not in match:
        match = None
    po_consumption = None
    if match:
        paid, reserved = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    InvoiceExecution.status == "SETTLED",
                                    case(
                                        (POLedger.kind == "RESERVE", POLedger.amount_cents),
                                        else_=-POLedger.amount_cents,
                                    ),
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (POLedger.kind == "RESERVE", POLedger.amount_cents),
                                else_=-POLedger.amount_cents,
                            )
                        ),
                        0,
                    ),
                )
                .select_from(POLedger)
                # po_ledger.execution_id has no FK, so SQLAlchemy cannot infer this join.
                .join(InvoiceExecution, InvoiceExecution.execution_id == POLedger.execution_id)
                .where(POLedger.po_id == row.po_id)
            )
        ).one()
        po_consumption = {
            "accepted_value_cents": match["accepted_value_cents"],
            # SUM(bigint) is NUMERIC in Postgres (Decimal); cents are int end to end (R4).
            "paid_cents": int(paid),
            "reserved_cents": int(reserved - paid),
            "this_payable_cents": row.approved_cents,
        }
    gate = None
    dispatcher = getattr(request.app.state, "dispatcher", None)
    timeline, anchor = await _settlement_view(session, execution_id)
    await session.commit()
    invoice_status = (
        await _live_invoice_status(request, row, match["paypal_invoice_status"]) if match else None
    )
    if dispatcher and row.status != "DUPLICATE_BLOCKED":
        snapshot = await dispatcher.snapshot(execution_id)
        gate = snapshot.values.get("gate")
        if gate and row.status == "FROZEN":
            pending = any(
                task.name == "human_review" and task.interrupts and not task.error
                for task in snapshot.tasks
            )
            if not pending:
                gate = (
                    {**gate.model_dump(), "allowed_actions": []}
                    if hasattr(gate, "model_dump")
                    else {
                        **gate,
                        "allowed_actions": [],
                    }
                )
    return CaseDetail(
        execution_id=row.execution_id,
        status=row.status,
        replayed=row.replayed,
        duplicate_of_execution_id=duplicate,
        uploaded_by=doc.uploaded_by,
        viewer={"operator_id": viewer.operator_id, "role": viewer.role},
        vendor={
            "vendor_id": vendor.vendor_id,
            "name": vendor.vendor_name,
            "version": vendor.version,
            "paypal_payer_id": vendor.paypal_payer_id,
            "onboarded_at": vendor.onboarded_at.isoformat(),
            "change_requested_by": None,
            "change_approved_by": None,
            "change_effective_at": None,
        }
        if vendor
        else None,
        purchase_order={
            "po_id": po.po_id,
            "version": po.version,
            "description": po.item_name,
            "ordered_qty": po.ordered_qty,
            "unit_price_cents": po.unit_price_cents,
        }
        if po
        else None,
        goods_receipt={
            "grn_id": grn.grn_id,
            "version": 1,
            "recorded_by": grn.recorded_by,
            "accepted_qty": match["accepted_qty"],
        }
        if grn and match
        else None,
        paypal_invoice={
            "invoice_id": match["paypal_invoice_id"],
            "status": invoice_status,
            "billed_qty": match["billed_qty"],
            "unit_price_cents": match["billed_unit_price_cents"],
            "total_cents": match["billed_cents"],
            "payee_reference": None,
        }
        if match and invoice_status
        else None,
        po_consumption=po_consumption,
        policy=policy.compiled_json if policy and policy.version == row.policy_version else None,
        extraction=doc.extraction_json,
        forensics=doc.forensics_json,
        match=match,
        risk=row.risk_json,
        evidence=_evidence_view(row.evidence_pack_json, gate),
        short_pay=row.short_pay_json,
        short_pay_balance=row.short_pay_balance or "NONE",
        gate=gate,
        approvals=[
            {
                "approval_id": vote.approval_id,
                "actor": {"operator_id": actor.operator_id, "role": actor.role},
                "action": vote.action,
                "reason": vote.reason,
                "gate_hash": vote.gate_hash,
                "void_reason": "Approval voided" if vote.voided_at else None,
            }
            for vote, actor in approvals
        ],
        settlement_timeline=timeline,
        audit_anchor=anchor,
    )


def _evidence_view(evidence: dict | None, gate) -> dict | None:
    """R14: show A4's advisory recommendation only when the gate is known and not HIGH_RISK."""
    if evidence is None:
        return None
    reasons = None if gate is None else getattr(gate, "reasons", None)
    if reasons is None and isinstance(gate, dict):
        reasons = gate.get("reasons")
    if reasons is None or "HIGH_RISK" in reasons:
        return {**evidence, "advisory_recommendation": None}
    return evidence


@router.post(
    "/{execution_id}/decisions",
    response_model=DecisionReceipt,
    operation_id="submit_decision",
    responses={409: {"model": APIError, "description": "Case changed; review again"}},
)
async def submit_decision(
    execution_id: str,
    decision: DecisionRequest,
    request: Request,
    actor: Annotated[Operator, Depends(roles("controller", "cfo"))],
):
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is None:
        raise HTTPException(503, "Decision dispatch unavailable")
    try:
        return DecisionReceipt(
            approval_id=await dispatcher.decide(execution_id, actor.operator_id, decision)
        )
    except DecisionConflict as error:
        raise HTTPException(409, str(error)) from None
    except DispatchUnavailable:
        raise HTTPException(503, "Decision dispatch unavailable") from None
