"""P2-06: structured billing wins, receiving/PO facts stay trusted, duplicates fail closed."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.decision.match import (
    MatchUnavailable,
    build_match_report,
    match,
    normalize_invoice_number,
    within_two_edits,
)
from app.models.core import GoodsReceipt, MandatePolicy, PurchaseOrder, VendorMaster
from app.models.payables import InvoiceExecution, POLedger, SpendLedger
from app.paypal.invoicing import Invoice, InvoiceItem
from app.schemas.decision import InvoiceExtraction
from tests.seed import POLICY_ID, make_execution, seed_world

NOW = datetime(2026, 10, 28, 1, tzinfo=UTC)


def extraction(**kw):
    return InvoiceExtraction(
        **{
            "vendor_invoice_number": "INV-001042",
            "po_reference": "PO-1042",
            "payee_email": "supplier@x.example",
            "billed_qty": 100,
            "unit_price_cents": 480,
            "total_cents": 48000,
            "currency": "USD",
            "field_confidence": {},
            **kw,
        }
    )


def invoice(**kw):
    return Invoice(
        **{
            "invoice_id": "INV2-ABC",
            "status": "SENT",
            "invoice_number": "INV-1042",
            "reference": "PO-1042",
            "currency": "USD",
            "invoicer_email": "supplier@x.example",
            "total_cents": 48000,
            "paid_cents": 0,
            "due_cents": 48000,
            "items": (InvoiceItem(100, 480),),
            "issues": (),
            **kw,
        }
    )


def report(**kw):
    return build_match_report(
        **{
            "extraction": extraction(),
            "invoice": invoice(),
            "vendor": VendorMaster(
                vendor_id="v1",
                version=3,
                trust_status="VERIFIED",
                verified_paypal_email="supplier@x.example",
                onboarded_at=NOW - timedelta(days=90),
            ),
            "po": PurchaseOrder(
                po_id="PO-1042",
                vendor_id="v1",
                version=2,
                ordered_qty=100,
                unit_price_cents=480,
                currency="USD",
                status="OPEN",
            ),
            "receipts": [GoodsReceipt(grn_id="grn-1", po_id="PO-1042", accepted_qty=85)],
            "po_committed_cents": 5000,
            "vendor_invoices_today": 2,
            "vendor_committed_today_cents": 1000,
            "now": NOW,
            **kw,
        }
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" inv 2026-001042 / A ", "INV2026001042A"),
        ("A000B00001", "A0B1"),
        ("  -_/.\t", None),
        (None, None),
    ],
)
def test_normalization(raw, expected):
    assert normalize_invoice_number(raw) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("INV1042", "INV1042A", True),
        ("INV1042", "INV1043", True),
        ("A", "ABC", True),
        ("ABC", "", False),
        ("ABCDEF", "ABXYZF", False),
    ],
)
def test_edit_distance(a, b, expected):
    assert within_two_edits(a, b) is expected


def test_report_uses_structured_billing_and_receipt_price():
    result = report(extraction=extraction(total_cents=999999, billed_qty=999))
    assert (result.billed_cents, result.billed_qty, result.accepted_qty) == (48000, 100, 85)
    assert result.accepted_value_cents == 40800
    assert result.po_remaining_cents == 35800
    assert result.invoice_number_norm == "INV1042"
    assert set(result.mismatched_fields) >= {"total_cents", "billed_qty"}
    assert not result.paypal_invoice_matches_pdf


def test_missing_invoice_uses_pdf_only_as_unverified_evidence():
    result = report(invoice=None)
    assert result.billed_cents == 48000 and result.paypal_invoice_id is None
    assert not result.paypal_invoice_matches_pdf
    assert "paypal_invoice" in result.mismatched_fields


@pytest.mark.parametrize(
    "bad",
    [
        {"currency": "EUR"},
        {"total_cents": None},
        {"items": (InvoiceItem(None, 480),)},
        {"items": (InvoiceItem(100, None),)},
        {"items": (InvoiceItem(50, 480), InvoiceItem(50, 480))},
        {"issues": ("amount: not an exact USD amount",)},
    ],
)
def test_invalid_structured_precision_cannot_be_replaced_by_pdf(bad):
    result = report(invoice=invoice(**bad))
    assert not result.precision_ok and not result.paypal_invoice_matches_pdf
    if "total_cents" in bad:
        assert result.billed_cents == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"reference": "PO-OTHER"},
        {"invoicer_email": "attacker@example.invalid"},
        {"invoice_number": "INV-OTHER"},
        {"invoice_number": None},
    ],
)
def test_structured_identity_mismatches_fail_source_check(bad):
    assert not report(invoice=invoice(**bad)).paypal_invoice_matches_pdf


@pytest.mark.parametrize("email", [None, "", "supp1ier@x.example", "ѕuррlіеr@х.ехаmрlе"])
def test_missing_or_changed_pdf_payee_is_never_master_match(email):
    result = report(extraction=extraction(payee_email=email))
    assert not result.payee_matches_vendor_master
    assert result.payee_lookalike is bool(email)


@pytest.mark.parametrize("status", ["COOLING", "BLOCKED"])
def test_non_verified_vendor_cannot_look_payable(status):
    vendor = VendorMaster(
        vendor_id="v1",
        version=3,
        trust_status=status,
        verified_paypal_email="supplier@x.example",
        onboarded_at=NOW - timedelta(days=90),
    )
    assert not report(vendor=vendor).payee_matches_vendor_master


@pytest.mark.parametrize("qty", [True, "100", 100.0, 1.5])
def test_extraction_quantity_is_strict(qty):
    with pytest.raises(ValidationError):
        extraction(billed_qty=qty)


@pytest.fixture
async def match_world(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        await seed_world(session)
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
        session.add(
            make_execution(
                "current", "ANALYZING", created_at=NOW, paypal_invoice_id="INV2-ABC", grn_id="grn-1"
            )
        )
    reader = AsyncMock()
    reader.get_invoice.return_value = invoice()
    return pg_sessionmaker, reader


async def run_match(world):
    sm, reader = world
    async with sm() as session:
        return (
            await match(
                {"execution_id": "current", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )
        )["match"]


async def test_match_reloads_db_and_never_writes(match_world):
    sm, reader = match_world
    async with sm() as session:
        old = (await session.execute(select(PurchaseOrder))).scalar_one()
        async with sm() as other, other.begin():
            await other.execute(text("UPDATE purchase_orders SET unit_price_cents=500,version=2"))
        result = (
            await match(
                {
                    "execution_id": "current",
                    "extraction": extraction(),
                    "po_id": "attacker-po",
                    "vendor_id": "attacker-vendor",
                },
                session=session,
                reader=reader,
                now=NOW,
            )
        )["match"]
        assert old.version == 2 and result.po_unit_price_cents == 500
        assert result.po_id == "PO-1042" and not session.dirty and not session.new
        row = await session.get(InvoiceExecution, "current")
        assert row.match_report_json is None and row.billed_cents is None
    reader.get_invoice.assert_awaited_once_with("INV2-ABC", execution_id="current")


async def test_exact_duplicate_by_normalized_number_and_paypal_id(match_world):
    sm, _ = match_world
    async with sm() as session, session.begin():
        session.add(
            make_execution(
                "original",
                "SETTLED",
                invoice_number_norm="INV1042",
                created_at=NOW - timedelta(days=100),
            )
        )
    assert (await run_match(match_world)).duplicate_of == "original"
    async with sm() as session, session.begin():
        original = await session.get(InvoiceExecution, "original")
        original.status = "REJECTED"
    assert (await run_match(match_world)).duplicate_of is None
    async with sm() as session, session.begin():
        current = await session.get(InvoiceExecution, "current")
        # Blocked resubmissions do not collide with the live original's unique key.
        current.status = "DUPLICATE_BLOCKED"
        await session.flush()
        session.add(make_execution("paypal-original", "SETTLED", paypal_invoice_id="INV2-ABC"))
    assert (await run_match(match_world)).duplicate_of == "paypal-original"


@pytest.mark.parametrize(
    ("amount", "days", "number", "expected"),
    [
        (48000, 30, "INV1042A", "near"),
        (48000, 31, "INV1042A", None),
        (44000, 1, "INV1043", "near"),
        (43000, 1, "INV1043", None),
        (48000, 1, "UNRELATED", None),
    ],
)
async def test_fuzzy_duplicate_boundaries(match_world, amount, days, number, expected):
    sm, _ = match_world
    async with sm() as session, session.begin():
        session.add(
            make_execution(
                "near",
                "SETTLED",
                invoice_number_norm=number,
                billed_cents=amount,
                created_at=NOW - timedelta(days=days),
            )
        )
    result = await run_match(match_world)
    assert result.fuzzy_duplicate_of == expected


async def test_current_receipts_and_own_reservations_on_resume(match_world):
    sm, _ = match_world
    async with sm() as session, session.begin():
        session.add(
            GoodsReceipt(
                grn_id="corrected",
                po_id="PO-1042",
                supersedes_grn_id="grn-1",
                received_qty=50,
                accepted_qty=40,
                rejected_qty=10,
                recorded_by="op-rcv",
                source="SEEDED",
                received_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
        session.add(
            GoodsReceipt(
                grn_id="delivery-2",
                po_id="PO-1042",
                received_qty=40,
                accepted_qty=30,
                rejected_qty=10,
                recorded_by="op-rcv",
                source="SEEDED",
                received_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
        session.add_all(
            [
                POLedger(
                    po_id="PO-1042",
                    execution_id="current",
                    kind="RESERVE",
                    qty=1,
                    amount_cents=1000,
                ),
                POLedger(
                    po_id="PO-1042", execution_id="other", kind="RESERVE", qty=1, amount_cents=2000
                ),
            ]
        )
    result = await run_match(match_world)
    assert result.accepted_qty == 70
    assert result.accepted_value_cents == 33600 and result.po_remaining_cents == 31600


async def test_vendor_daily_facts_use_mandate_business_day_and_net_spend(match_world):
    sm, _ = match_world
    day = (NOW - timedelta(hours=7)).date()
    async with sm() as session, session.begin():
        policy = await session.get(MandatePolicy, (POLICY_ID, 1))
        policy.cap_timezone = "America/Los_Angeles"
        session.add_all(
            [
                make_execution("same-day", "SETTLED", created_at=NOW - timedelta(hours=2)),
                make_execution("prior-day", "SETTLED", created_at=NOW - timedelta(days=1)),
                make_execution("replay", "SETTLED", created_at=NOW, replayed=True),
            ]
        )
        await session.flush()
        reserved = SpendLedger(
            execution_id="same-day",
            policy_id=POLICY_ID,
            vendor_id="v1",
            business_date=day,
            kind="RESERVE",
            amount_cents=5000,
        )
        session.add(reserved)
        await session.flush()
        session.add(
            SpendLedger(
                execution_id="same-day",
                policy_id=POLICY_ID,
                vendor_id="v1",
                business_date=day,
                kind="RELEASE",
                amount_cents=5000,
                reserve_entry_id=reserved.entry_id,
            )
        )
    result = await run_match(match_world)
    assert result.vendor_invoices_today == 2 and result.vendor_committed_today_cents == 0


async def test_missing_trusted_execution_fails_closed(match_world):
    sm, reader = match_world
    async with sm() as session:
        with pytest.raises(MatchUnavailable):
            await match(
                {"execution_id": "missing", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )
    reader.get_invoice.assert_not_awaited()


async def test_reader_errors_never_return_payable_evidence(match_world):
    sm, reader = match_world
    reader.get_invoice.side_effect = RuntimeError("unavailable")
    async with sm() as session:
        with pytest.raises(RuntimeError):
            await match(
                {"execution_id": "current", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )


async def test_no_paypal_binding_still_returns_unverified_pdf(match_world):
    sm, reader = match_world
    async with sm() as session, session.begin():
        execution = await session.get(InvoiceExecution, "current")
        execution.paypal_invoice_id = None
    result = await run_match(match_world)
    assert not result.paypal_invoice_matches_pdf and result.paypal_invoice_id is None
    assert result.invoice_number_norm == "INV1042"
    reader.get_invoice.assert_not_awaited()


async def test_missing_receipt_cannot_return_a_report(pg_sessionmaker):
    sm, reader = pg_sessionmaker, AsyncMock()
    reader.get_invoice.return_value = invoice()
    async with sm() as session, session.begin():
        await seed_world(session)
        session.add(make_execution("current", "ANALYZING", paypal_invoice_id="INV2-ABC"))
    async with sm() as session:
        with pytest.raises(MatchUnavailable):
            await match(
                {"execution_id": "current", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )


async def test_dirty_session_cannot_be_flushed_by_matching(match_world):
    sm, reader = match_world
    async with sm() as session:
        vendor = await session.get(VendorMaster, "v1")
        vendor.version = 99
        with pytest.raises(MatchUnavailable, match="CLEAN_SESSION"):
            await match(
                {"execution_id": "current", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )
    reader.get_invoice.assert_not_awaited()


async def test_repeatable_read_snapshot_is_rejected(match_world):
    sm, reader = match_world
    async with sm() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        with pytest.raises(MatchUnavailable, match="READ_COMMITTED"):
            await match(
                {"execution_id": "current", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )
    reader.get_invoice.assert_not_awaited()


async def test_invoice_response_must_match_trusted_binding(match_world):
    sm, reader = match_world
    reader.get_invoice.return_value = invoice(invoice_id="OTHER-INVOICE")
    async with sm() as session:
        with pytest.raises(MatchUnavailable, match="SOURCE_ID"):
            await match(
                {"execution_id": "current", "extraction": extraction()},
                session=session,
                reader=reader,
                now=NOW,
            )
