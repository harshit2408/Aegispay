"""A4 wiring for the decision graph (PRD §10, §11.3, R2, R14).

Facts are read once, async, then served to the model through a fixed read-only registry
(SPIKE-6 fallback: own read tools with the Agent Toolkit's names, see docs/TOOLS.md).
The live run reuses the execution's durable LLM budget row, so A4 shares A2/A3's $1 and
four-request cap across processes (§16). Output is advisory; the gate never reads it.
"""

import asyncio
import dataclasses
from collections.abc import Callable

from sqlalchemy import select

from app.agents.investigator import investigate
from app.agents.provider import governed_analysis
from app.decision.analyze import _budget_id
from app.models.core import PurchaseOrder, VendorMaster
from app.models.payables import InvoiceExecution
from app.paypal.toolkit import ReadTools, assert_read_only
from app.schemas.decision import EvidencePack

TOOL_NAMES = ("get_invoice", "get_purchase_order", "get_vendor", "list_prior_executions")
assert_read_only(TOOL_NAMES)  # R2: import of the graph wiring fails if a write name appears
PRIOR_LIMIT = 10


def read_tools(snapshot: dict, *, invoice_id, po_id, vendor_id) -> ReadTools:
    """Model-supplied ids are ignored: the scope pins every tool to this case."""
    return ReadTools(
        {
            "get_invoice": lambda invoice_id: snapshot["invoice"],
            "get_purchase_order": lambda po_id: snapshot["purchase_order"],
            "get_vendor": lambda vendor_id: snapshot["vendor"],
            "list_prior_executions": lambda vendor_id: snapshot["prior_executions"],
        },
        scope={"invoice_id": invoice_id, "po_id": po_id, "vendor_id": vendor_id},
    )


async def _snapshot(session, execution: InvoiceExecution, reader) -> dict:
    po = await session.get(PurchaseOrder, execution.po_id) if execution.po_id else None
    vendor = await session.get(VendorMaster, execution.vendor_id) if execution.vendor_id else None
    prior = (
        await session.execute(
            select(
                InvoiceExecution.execution_id,
                InvoiceExecution.status,
                InvoiceExecution.route,
                InvoiceExecution.billed_cents,
                InvoiceExecution.approved_cents,
                InvoiceExecution.risk_score,
                InvoiceExecution.created_at,
            )
            .where(
                InvoiceExecution.vendor_id == execution.vendor_id,
                InvoiceExecution.execution_id != execution.execution_id,
            )
            .order_by(InvoiceExecution.created_at.desc())
            .limit(PRIOR_LIMIT)
        )
    ).all()
    invoice = None
    if reader is not None and execution.paypal_invoice_id:
        found = await reader.get_invoice(
            execution.paypal_invoice_id, execution_id=execution.execution_id
        )
        invoice = dataclasses.asdict(found)
    return {
        "invoice": invoice,
        "purchase_order": po
        and {
            "po_id": po.po_id,
            "version": po.version,
            "vendor_id": po.vendor_id,
            "item_name": po.item_name,
            "ordered_qty": po.ordered_qty,
            "unit_price_cents": po.unit_price_cents,
            "currency": po.currency,
            "status": po.status,
        },
        # Payee identifiers stay out of the prompt; A4 cannot act on them anyway (R1).
        "vendor": vendor
        and {
            "vendor_id": vendor.vendor_id,
            "vendor_name": vendor.vendor_name,
            "trust_status": vendor.trust_status,
            "version": vendor.version,
            "onboarded_at": vendor.onboarded_at,
            "payee_last_changed_at": vendor.payee_last_changed_at,
        },
        "prior_executions": [row._asdict() for row in prior],
    }


def build_investigator(sessions, settings, reader=None, *, replay_complete=None, run=None):
    """Return async (execution_id, state) -> EvidencePack | None for the graph node.

    Replayed executions never reach a provider: they use `replay_complete` or get None.
    """
    run = run or governed_analysis

    async def investigator(execution_id: str, state: dict) -> EvidencePack | None:
        async with sessions() as session:
            execution = await session.get(InvoiceExecution, execution_id)
            if execution.replayed and replay_complete is None:
                return None  # R24: no live call and no recording -> "investigation unavailable"
            snapshot = await _snapshot(session, execution, None if execution.replayed else reader)
        tools = read_tools(
            snapshot,
            invoice_id=execution.paypal_invoice_id,
            po_id=execution.po_id,
            vendor_id=execution.vendor_id,
        )
        case = {
            "match": state["match"].model_dump(mode="json"),
            "risk": state["risk"].model_dump(mode="json"),
        }
        if execution.replayed:
            return await asyncio.to_thread(investigate, replay_complete, tools, case=case)

        def action(complete: Callable) -> EvidencePack | None:
            return investigate(complete, tools, case=case)

        return await asyncio.to_thread(
            run, _budget_id(execution_id), settings=settings, action=action
        )

    return investigator
