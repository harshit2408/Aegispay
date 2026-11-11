"""Authenticated ledger, case evidence and decision dispatch (PRD §13)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, roles
from app.db import get_session
from app.decision.dispatch import DecisionConflict, DispatchUnavailable
from app.models.core import GoodsReceipt, MandatePolicy, Operator, PurchaseOrder, VendorMaster
from app.models.payables import Approval, InvoiceDocument, InvoiceExecution, POLedger
from app.schemas.case import APIError, CaseDetail, DecisionReceipt, DecisionRequest, ExecutionRow

router = APIRouter(prefix="/api/executions", tags=["executions"])
Session = Annotated[AsyncSession, Depends(get_session)]
Viewer = Annotated[Operator, Depends(require_operator)]


@router.get("", response_model=list[ExecutionRow], operation_id="list_executions")
async def list_executions(session: Session, viewer: Viewer):
    result = await session.execute(
        select(InvoiceExecution, VendorMaster.vendor_name)
        .outerjoin(VendorMaster, VendorMaster.vendor_id == InvoiceExecution.vendor_id)
        .order_by(InvoiceExecution.created_at.desc(), InvoiceExecution.execution_id)
        .limit(200)
    )
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
                .join(InvoiceExecution, POLedger.execution_id == InvoiceExecution.execution_id)
                .where(POLedger.po_id == row.po_id)
            )
        ).one()
        po_consumption = {
            "accepted_value_cents": match["accepted_value_cents"],
            "paid_cents": int(paid),
            "reserved_cents": int(reserved - paid),
            "this_payable_cents": row.approved_cents,
        }
    gate = None
    dispatcher = getattr(request.app.state, "dispatcher", None)
    await session.commit()
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
            "status": match["paypal_invoice_status"],
            "billed_qty": match["billed_qty"],
            "unit_price_cents": match["billed_unit_price_cents"],
            "total_cents": match["billed_cents"],
            "payee_reference": None,
        }
        if match and match["paypal_invoice_status"]
        else None,
        po_consumption=po_consumption,
        policy=policy.compiled_json if policy and policy.version == row.policy_version else None,
        extraction=doc.extraction_json,
        forensics=doc.forensics_json,
        match=match,
        risk=row.risk_json,
        evidence=row.evidence_pack_json,
        short_pay=row.short_pay_json,
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
        settlement_timeline=[],
        audit_anchor={"verified": False, "anchored_at": None, "head_hmac": None},
    )


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
