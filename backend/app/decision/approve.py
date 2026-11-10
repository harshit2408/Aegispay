"""Commit the versioned approval, PO reservation and frozen settlement command together."""

import re

from sqlalchemy import insert, select, text

from app.decision.policy_gate import GateUnavailable, evaluate
from app.models.core import MandatePolicy, Operator, PurchaseOrder, VendorMaster
from app.models.payables import Approval, InvoiceExecution, SettlementControl, SettlementOutbox
from app.schemas.decision import (
    AegisState,
    ForensicsReport,
    GateDecision,
    MatchReport,
    RiskAssessment,
)
from app.schemas.decision import MandatePolicy as Policy
from app.vendor_sim.gateway import OPEN_STATUSES


async def approve_for_payment(
    sessions,
    transition,
    reserve_po,
    execution_id: str,
    state: AegisState,
    *,
    vendor_new_days: int = 7,
    max_vendor_invoices: int = 5,
) -> bool:
    """Return False when the locked DB facts no longer authorize the graph's decision."""
    match = MatchReport.model_validate(state["match"], strict=True)
    expected = GateDecision.model_validate(state["gate"], strict=True)
    async with sessions() as session, session.begin():
        execution = await session.get(InvoiceExecution, execution_id, with_for_update=True)
        if execution is None:
            return False
        command = await session.scalar(
            select(SettlementOutbox).where(SettlementOutbox.execution_id == execution_id)
        )
        if execution.approved_cents is not None:
            if execution.replayed:
                return execution.status == "APPROVED_FOR_PAYMENT" and command is None
            return command is not None and command.amount_cents == execution.approved_cents
        if (
            command is not None
            or execution.status not in ("ANALYZING", "FROZEN")
            or not execution.po_id
            or not execution.vendor_id
            or not execution.paypal_invoice_id
            or match.paypal_invoice_id != execution.paypal_invoice_id
            or match.paypal_invoice_status not in OPEN_STATUSES
            or not match.precision_ok
            or execution.invoice_number_norm != match.invoice_number_norm
        ):
            return False

        # Same mandate → PO order as the settlement worker. NO KEY UPDATE keeps FK inserts open.
        mandate = await session.get(
            MandatePolicy,
            (execution.policy_id, execution.policy_version),
            with_for_update={"key_share": True},
        )
        if mandate is None or not mandate.is_active:
            return False
        po = await session.get(PurchaseOrder, execution.po_id, with_for_update=True)
        if po is None or not await session.scalar(
            text("SELECT public.aegis_lock_vendor(:vendor_id)"),
            {"vendor_id": execution.vendor_id},
        ):
            return False
        vendor = await session.get(VendorMaster, execution.vendor_id)
        control = await session.get(SettlementControl, True, with_for_update={"read": True})
        if (
            vendor is None
            or vendor.trust_status != "VERIFIED"
            or not re.fullmatch(r"[A-Za-z0-9]{5,32}", vendor.paypal_payer_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", execution_id)
            or control is None
            or control.halted
        ):
            return False
        await session.execute(
            select(Operator.operator_id)
            .join(Approval, Approval.actor == Operator.operator_id)
            .where(Approval.execution_id == execution_id, Approval.voided_at.is_(None))
            .order_by(Operator.operator_id)
            .with_for_update(read=True, of=Operator)
        )
        try:
            gate = await evaluate(
                session,
                execution_id=execution_id,
                policy=Policy.model_validate(state["policy"], strict=True),
                match=match,
                risk=RiskAssessment.model_validate(state["risk"], strict=True),
                forensics=ForensicsReport.model_validate(state["forensics"], strict=True),
                vendor_new_days=vendor_new_days,
                max_vendor_invoices=max_vendor_invoices,
            )
        except GateUnavailable:
            return False
        if gate != expected or gate.route not in ("AUTO_SETTLE", "SHORT_PAY"):
            return False

        if not execution.replayed:
            await reserve_po(
                session,
                execution_id=execution_id,
                po_id=po.po_id,
                amount_cents=gate.payable_cents,
            )
            await session.execute(
                insert(SettlementOutbox).values(
                    command_id=f"CMD-{execution_id}",
                    execution_id=execution_id,
                    receiver_payer_id=vendor.paypal_payer_id,
                    vendor_version=vendor.version,
                    amount_cents=gate.payable_cents,
                    status="PENDING",
                )
            )
        execution.route = gate.route
        execution.billed_cents = match.billed_cents
        execution.approved_cents = gate.payable_cents
        execution.vendor_version = vendor.version
        execution.po_version = po.version
        execution.grn_id = match.grn_id
        await transition(session, execution, "APPROVED_FOR_PAYMENT", actor="system:decision")
    return True
