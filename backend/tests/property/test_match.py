"""R3/R4/R9: trusted cents and canonical invoice identity, generated cases."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from app.decision.match import build_match_report, normalize_invoice_number, within_two_edits
from app.models.core import GoodsReceipt, PurchaseOrder, VendorMaster
from app.paypal.invoicing import Invoice, InvoiceItem
from app.schemas.decision import InvoiceExtraction


@given(st.integers(0, 10**18), st.integers(0, 30))
def test_numeric_zero_padding_and_punctuation_cannot_bypass_identity(number, zeros):
    canonical = normalize_invoice_number(f"INV{number}")
    assert normalize_invoice_number(f" inv -_/{'0' * zeros}{number}. ") == canonical
    assert normalize_invoice_number(canonical) == canonical


@given(st.text(alphabet="ABC012", max_size=20), st.text(alphabet="ABC012", max_size=20))
def test_bounded_distance_matches_full_reference(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        nxt = [i]
        for j, right in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = nxt
    assert within_two_edits(a, b) is (row[-1] <= 2)


@given(
    pdf=st.tuples(st.integers(0, 10**6), st.integers(0, 10**12), st.integers(0, 10**12)),
    quantities=st.lists(st.integers(0, 1000), min_size=1, max_size=5),
    ordered=st.integers(1, 1000),
    price=st.integers(1, 10**8),
    committed=st.integers(0, 10**12),
)
def test_pdf_never_changes_structured_billing_or_trusted_remaining_value(
    pdf, quantities, ordered, price, committed
):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    report = build_match_report(
        extraction=InvoiceExtraction(
            vendor_invoice_number="N",
            po_reference="PO",
            payee_email="supplier@example.invalid",
            billed_qty=pdf[0],
            unit_price_cents=pdf[1],
            total_cents=pdf[2],
            currency="USD",
            field_confidence={},
        ),
        invoice=Invoice(
            invoice_id="INV2-ABC",
            status="SENT",
            invoice_number="N",
            reference="PO",
            currency="USD",
            invoicer_email="supplier@example.invalid",
            total_cents=48000,
            paid_cents=0,
            due_cents=48000,
            items=(InvoiceItem(100, 480),),
            issues=(),
        ),
        vendor=VendorMaster(
            vendor_id="v",
            version=1,
            trust_status="VERIFIED",
            onboarded_at=now,
            verified_paypal_email="supplier@example.invalid",
        ),
        po=PurchaseOrder(
            po_id="PO",
            vendor_id="v",
            version=1,
            ordered_qty=ordered,
            unit_price_cents=price,
            currency="USD",
            status="OPEN",
        ),
        receipts=[
            GoodsReceipt(grn_id=str(i), po_id="PO", accepted_qty=q)
            for i, q in enumerate(quantities)
        ],
        po_committed_cents=committed,
        vendor_invoices_today=1,
        vendor_committed_today_cents=0,
        now=now,
    )
    accepted_value = min(sum(quantities), ordered) * price
    assert (report.billed_qty, report.billed_unit_price_cents, report.billed_cents) == (
        100,
        480,
        48000,
    )
    assert report.accepted_value_cents == accepted_value
    assert report.po_remaining_cents == max(0, accepted_value - committed)
    assert 0 <= report.po_remaining_cents <= accepted_value
    assert all(
        type(value) is int
        for value in (report.billed_cents, report.accepted_value_cents, report.po_remaining_cents)
    )
