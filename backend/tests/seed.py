"""Postgres fixtures for ledger/settlement tests: operators, vendor, PO, mandate, document."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import GoodsReceipt, MandatePolicy, Operator, PurchaseOrder, VendorMaster
from app.models.payables import InvoiceDocument, InvoiceExecution, SettlementOutbox

PAYER_ID = "PAYER12345678"
POLICY_ID = "pol-1"


async def seed_world(session: AsyncSession, *, daily_cap_cents: int = 500_000) -> None:
    """Three operators (author != activator), a verified vendor, a PO and one active mandate."""
    now = datetime.now(UTC)
    session.add_all(
        [
            Operator(
                operator_id="op-ctl",
                display_name="Controller",
                role="controller",
                password_hash="x",
            ),
            Operator(operator_id="op-cfo", display_name="CFO", role="cfo", password_hash="x"),
            Operator(operator_id="op-adm", display_name="Admin", role="admin", password_hash="x"),
            Operator(
                operator_id="op-rcv", display_name="Receiver", role="receiver", password_hash="x"
            ),
        ]
    )
    await session.flush()
    session.add_all(
        [
            VendorMaster(
                vendor_id="v1",
                vendor_name="Supplier",
                paypal_payer_id=PAYER_ID,
                verified_paypal_email="supplier@x.example",
                onboarded_at=now,
                onboarding_evidence={"how": "test"},
            ),
            MandatePolicy(
                policy_id=POLICY_ID,
                version=1,
                raw_nl_prompt="test",
                compiled_json={},
                max_single_invoice_cents=100_000,
                daily_cap_cents=daily_cap_cents,
                cap_timezone="UTC",
                is_active=True,
                created_by="op-ctl",
                activated_by="op-cfo",
                activated_at=now,
            ),
        ]
    )
    await session.flush()
    session.add(
        PurchaseOrder(
            po_id="PO-1042",
            vendor_id="v1",
            sku_id="sku",
            item_name="Bracket",
            ordered_qty=100,
            unit_price_cents=480,
            created_by="op-adm",
        )
    )
    session.add(
        InvoiceDocument(
            document_id="doc-1",
            source="PDF_UPLOAD",
            sha256="0" * 64,
            storage_path="/x",
            uploaded_by="op-ctl",
        )
    )
    await session.flush()


def make_execution(
    execution_id: str, status: str = "APPROVED_FOR_PAYMENT", **kw
) -> InvoiceExecution:
    """An execution row for the seeded world; override any column via kwargs."""
    fields = {
        "execution_id": execution_id,
        "document_id": "doc-1",
        "po_id": "PO-1042",
        "vendor_id": "v1",
        "langgraph_thread_id": f"thread-{execution_id}",
        "policy_id": POLICY_ID,
        "policy_version": 1,
        "detector_enabled": True,
        "status": status,
    }
    return InvoiceExecution(**{**fields, **kw})


async def seed_approved(
    session: AsyncSession, execution_id: str = "e1", amount_cents: int = 48_000, **kw
) -> None:
    """An APPROVED_FOR_PAYMENT execution with its GRN, invoice id and frozen outbox command."""
    if await session.get(GoodsReceipt, "grn-1") is None:
        session.add(
            GoodsReceipt(
                grn_id="grn-1",
                po_id="PO-1042",
                received_qty=100,
                accepted_qty=100,
                rejected_qty=0,
                recorded_by="op-rcv",
                source="SEEDED",
                received_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await session.flush()
    fields = {
        "grn_id": "grn-1",
        "paypal_invoice_id": "INV2-ABC",
        "approved_cents": amount_cents,
        "billed_cents": amount_cents,
        "vendor_version": 1,
        "po_version": 1,
    }
    session.add(make_execution(execution_id, **{**fields, **kw}))
    await session.flush()
    session.add(
        SettlementOutbox(
            command_id=f"cmd-{execution_id}",
            execution_id=execution_id,
            amount_cents=amount_cents,
            receiver_payer_id=PAYER_ID,
            vendor_version=1,
            status="PENDING",
        )
    )
    await session.flush()
