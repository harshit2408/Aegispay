"""P1-12: the case read model reports what settlement actually did, and s1_headless can judge it."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.auth import COOKIE, token_hash
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models.auth import OperatorSession
from app.models.core import MandatePolicy
from app.models.payables import AuditAnchor, ExecutionEvent, Payout, POLedger
from app.paypal.client import PayPalError
from app.schemas.decision import MandatePolicy as Policy
from tests.seed import seed_approved, seed_world

spec = importlib.util.spec_from_file_location(
    "s1_headless", Path(__file__).parents[2] / "scripts" / "s1_headless.py"
)
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)

TOKEN = "synthetic-case-view-session"
MATCH = {
    "po_id": "PO-1042",
    "po_version": 1,
    "grn_id": "grn-1",
    "vendor_version": 1,
    "ordered_qty": 100,
    "billed_qty": 100,
    "accepted_qty": 100,
    "po_unit_price_cents": 480,
    "billed_unit_price_cents": 480,
    "billed_cents": 48000,
    "accepted_value_cents": 48000,
    "po_remaining_cents": 48000,
    "payee_matches_vendor_master": True,
    "payee_lookalike": False,
    "paypal_invoice_id": "INV2-ABC",
    "paypal_invoice_status": "SENT",
    "paypal_invoice_matches_pdf": True,
    "mismatched_fields": [],
    "precision_ok": True,
    "vendor_age_days": 90,
    "vendor_invoices_today": 0,
    "vendor_committed_today_cents": 0,
    "invoice_number_norm": "AP1042",
    "duplicate_of": None,
    "fuzzy_duplicate_of": None,
}
CHAIN = ("PAYOUT_SENDING", "PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED", "SETTLED")


def paypal_invoice(status):
    invoice = MagicMock()
    invoice.status = status
    return invoice


async def seed_settled(
    sm, *, item_status="SUCCESS", anchor_seq=10, chain=CHAIN, status="SETTLED", approved=48000
):
    now = datetime.now(UTC)
    async with sm.begin() as s:
        await seed_world(s)
        await seed_approved(
            s, status=status, billed_cents=approved, match_report_json=MATCH, amount_cents=approved
        )
        (await s.get(MandatePolicy, ("pol-1", 1))).compiled_json = Policy(
            policy_id="pol-1",
            version=1,
            daily_cap_cents=500000,
            max_single_invoice_cents=100000,
            vendor_daily_cap_cents=150000,
        ).model_dump(mode="json")
        s.add(
            POLedger(
                po_id="PO-1042", execution_id="e1", kind="RESERVE", qty=100, amount_cents=approved
            )
        )
        s.add(
            Payout(
                payout_id="p1",
                execution_id="e1",
                sender_batch_id="aegis-e1-payout",
                payout_batch_id="BATCH1",
                payout_item_id="ITEM1",
                receiver="PAYER12345678",
                amount_cents=approved,
                item_status=item_status,
            )
        )
        s.add(
            OperatorSession(
                token_hash=token_hash(TOKEN),
                operator_id="op-ctl",
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await s.flush()
        for seq, event_type in enumerate(("RECEIVED", "ANALYZING", *chain), start=5):
            s.add(
                ExecutionEvent(
                    event_seq=seq,
                    execution_id="e1",
                    actor="system:test",
                    event_type=event_type,
                    payload={},
                    prev_hash="0" * 64,
                    hash=f"{seq:064d}",
                )
            )
        if anchor_seq is not None:
            s.add(
                AuditAnchor(event_seq=anchor_seq, head_hash=f"{anchor_seq:064d}", external_ref="c1")
            )


@pytest.fixture
async def case(pg_sessionmaker, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/unused")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-case-view-secret-key-12345")
    get_settings.cache_clear()

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, sessions)
    reader = AsyncMock()
    reader.get_invoice.return_value = paypal_invoice("MARKED_AS_PAID")
    monkeypatch.setattr(app.state, "vendor_reader", reader, raising=False)
    monkeypatch.setattr(app.state, "dispatcher", None, raising=False)

    async def get():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            cookies={COOKIE: TOKEN},
        ) as client:
            response = await client.get("/api/executions/e1")
        assert response.status_code == 200, response.text
        return response.json()

    get.reader = reader
    yield get
    get_settings.cache_clear()


async def test_settled_case_is_judged_s1_pass(pg_sessionmaker, case):
    await seed_settled(pg_sessionmaker)
    body = await case()
    assert s1.verdict(body) == []
    steps = {e["status"]: e for e in body["settlement_timeline"]}
    assert list(steps) == list(CHAIN)  # internal RECEIVED/ANALYZING are not settlement steps
    assert steps["PAYOUT_SUBMITTED"]["paypal_id"] == "BATCH1"
    assert steps["PAYOUT_SENDING"]["verified"] is False  # no PayPal receipt yet
    assert body["audit_anchor"]["verified"] and body["audit_anchor"]["head_hmac"] == f"{10:064d}"
    assert body["po_consumption"]["paid_cents"] == 48000
    case.reader.get_invoice.assert_awaited_once_with("INV2-ABC")


async def test_unanchored_tail_is_not_verified(pg_sessionmaker, case):
    await seed_settled(pg_sessionmaker, anchor_seq=9)  # last event is seq 10
    body = await case()
    assert body["audit_anchor"]["verified"] is False
    assert "audit anchor not verified" in s1.verdict(body)


async def test_no_anchor_receipt_is_not_verified(pg_sessionmaker, case):
    await seed_settled(pg_sessionmaker, anchor_seq=None)
    assert (await case())["audit_anchor"] == {
        "verified": False,
        "anchored_at": None,
        "head_hmac": None,
    }


async def test_success_event_without_get_confirmed_item_is_unverified(pg_sessionmaker, case):
    await seed_settled(pg_sessionmaker, item_status="PENDING")
    body = await case()
    assert any("PAYOUT_SUCCEEDED not a verified" in p for p in s1.verdict(body))


async def test_invoice_not_paid_fails_verdict_and_is_never_guessed(pg_sessionmaker, case):
    await seed_settled(pg_sessionmaker)
    case.reader.get_invoice.return_value = paypal_invoice("SENT")
    assert "PayPal invoice not PAID/MARKED_AS_PAID" in s1.verdict(await case())


async def test_invoice_lookup_failure_falls_back_to_match_time_status(pg_sessionmaker, case):
    await seed_settled(pg_sessionmaker)
    case.reader.get_invoice.side_effect = PayPalError(500, "dbg", {})
    assert (await case())["paypal_invoice"]["status"] == "SENT"


async def test_invoice_lookup_transport_error_falls_back_instead_of_500(pg_sessionmaker, case):
    # PayPalClient re-raises httpx.TransportError after its retries; the case view must stay up.
    await seed_settled(pg_sessionmaker)
    case.reader.get_invoice.side_effect = httpx.ConnectError("paypal unreachable")
    assert (await case())["paypal_invoice"]["status"] == "SENT"


async def test_invoice_is_not_fetched_before_payout_success(pg_sessionmaker, case):
    await seed_settled(
        pg_sessionmaker, status="PAYOUT_SUBMITTED", chain=CHAIN[:2], item_status="PENDING"
    )
    body = await case()
    assert body["paypal_invoice"]["status"] == "SENT"
    case.reader.get_invoice.assert_not_awaited()
    assert [e["status"] for e in body["settlement_timeline"]] == list(CHAIN[:2])
