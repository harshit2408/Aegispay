"""Pre-payout recheck (PRD R20): nothing the approval relied on may have changed.

Each check returns a short reason code, or None when everything still holds. The DB checks run
inside the write-ahead transaction; the invoice check is an HTTP read, so it runs before it
(R19: no DB lock across an HTTP call).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import GoodsReceipt, PurchaseOrder, VendorMaster
from app.models.payables import InvoiceExecution, SettlementOutbox
from app.settlement import po_ledger
from app.settlement.halt import is_halted
from app.vendor_sim.gateway import VendorReader


async def db_recheck(
    session: AsyncSession, execution: InvoiceExecution, command: SettlementOutbox
) -> str | None:
    if await is_halted(session):
        return "HALTED"
    if execution.status not in ("APPROVED_FOR_PAYMENT", "PAYOUT_SENDING"):
        return "EXECUTION_CHANGED"
    if execution.replayed:
        return "REPLAYED_EXECUTION"  # R24: replays never move money
    if execution.approved_cents != command.amount_cents:
        return "AMOUNT_CHANGED"
    vendor = await session.get(VendorMaster, execution.vendor_id) if execution.vendor_id else None
    if vendor is None:
        return "NO_VENDOR"
    if vendor.trust_status != "VERIFIED":
        return "VENDOR_NOT_VERIFIED"
    if vendor.version != command.vendor_version or vendor.version != execution.vendor_version:
        return "VENDOR_VERSION_CHANGED"
    if vendor.paypal_payer_id != command.receiver_payer_id:
        return "PAYEE_CHANGED"
    po = await session.get(PurchaseOrder, execution.po_id) if execution.po_id else None
    if po is None or po.version != execution.po_version:
        return "PO_CHANGED"
    if po.vendor_id != execution.vendor_id:
        return "PO_VENDOR_CHANGED"
    if po.status != "OPEN":
        return "PO_CLOSED"
    if execution.grn_id is None:
        return "NO_GRN"
    grn = await session.get(GoodsReceipt, execution.grn_id)
    if grn is None or grn.po_id != po.po_id:
        return "GRN_CHANGED"
    superseded = await session.execute(
        select(GoodsReceipt.grn_id).where(GoodsReceipt.supersedes_grn_id == execution.grn_id)
    )
    if superseded.first() is not None:
        return "GRN_SUPERSEDED"
    reserved = await po_ledger.open_reserve(session, execution.execution_id)
    own_cents = reserved.amount_cents if reserved else 0
    if await po_ledger.committed_cents(
        session, po.po_id
    ) - own_cents + command.amount_cents > await po_ledger.accepted_value_cents(session, po):
        return "PO_EXHAUSTED"
    return None


async def invoice_recheck(reader: VendorReader, execution: InvoiceExecution) -> str | None:
    if not execution.paypal_invoice_id:
        return "NO_INVOICE"
    invoice = await reader.get_invoice(
        execution.paypal_invoice_id, execution_id=execution.execution_id
    )
    if not invoice.is_open:
        return "INVOICE_NOT_OPEN"
    if invoice.total_cents is None or invoice.currency != "USD":
        return "INVOICE_AMOUNT_INVALID"
    if execution.billed_cents is not None and invoice.total_cents != execution.billed_cents:
        return "INVOICE_AMOUNT_CHANGED"
    remaining = invoice.due_cents
    if remaining is None and invoice.paid_cents is not None:
        remaining = invoice.total_cents - invoice.paid_cents
    if remaining is None or execution.approved_cents > remaining:
        return "INVOICE_BALANCE_CHANGED"
    return None
