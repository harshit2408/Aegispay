"""P1-08: payout item state machine (PRD §9.3), R6 monotonic state, R7 record-payment once."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select, text

from app.models.payables import (
    InvoiceExecution,
    Payout,
    SettlementControl,
    SettlementOutbox,
    SpendLedger,
)
from app.paypal.client import PayPalClient
from app.paypal.invoicing import Invoicing
from app.paypal.payouts import Payouts
from app.settlement import spend_ledger
from app.settlement.reconciler import Reconciler, run_forever
from app.vendor_sim.gateway import VendorGateway
from tests.seed import PAYER_ID, POLICY_ID, seed_approved, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")
BASE = "https://api-m.sandbox.paypal.com"
BATCH, ITEM, INVOICE = "BATCH123ABC", "ITEM1", "INV2-ABC"
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


async def _no_sleep(_: float) -> None:
    return None


def item_json(status, receiver=PAYER_ID, cents="480.00"):
    return {
        "payout_item_id": ITEM,
        "payout_batch_id": BATCH,
        "transaction_status": status,
        "payout_item": {
            "recipient_type": "PAYPAL_ID",
            "receiver": receiver,
            "amount": {"value": cents, "currency": "USD"},
            "sender_item_id": "e1-1",
        },
    }


def invoice_json(status="SENT", paid="0.00"):
    return {
        "id": INVOICE,
        "status": status,
        "detail": {"invoice_number": "N", "reference": "PO-1042", "currency_code": "USD"},
        "items": [],
        "amount": {"currency_code": "USD", "value": "480.00"},
        "payments": {"paid_amount": {"currency_code": "USD", "value": paid}},
    }


def paypal_client(app):
    return PayPalClient(app=app, base_url=BASE, client_id="a", client_secret="b", sleep=_no_sleep)


class Env:
    def __init__(self, sm, router, clock=lambda: NOW):
        self.sm = sm
        invoicing = Invoicing(paypal_client("VENDOR"))
        self.rec = Reconciler(
            sm, Payouts(paypal_client("TREASURY")), VendorGateway(invoicing), clock=clock
        )
        router.post(f"{BASE}/v1/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        )
        self.item = router.get(f"{BASE}/v1/payments/payouts-item/{ITEM}").mock(
            return_value=httpx.Response(200, json=item_json("PENDING"))
        )
        self.invoice = router.get(f"{BASE}/v2/invoicing/invoices/{INVOICE}").mock(
            return_value=httpx.Response(200, json=invoice_json())
        )
        self.record = router.post(f"{BASE}/v2/invoicing/invoices/{INVOICE}/payments").mock(
            return_value=httpx.Response(200, json={"payment_id": "P1"})
        )

    def set_item(self, status, **kw):
        self.item.mock(return_value=httpx.Response(200, json=item_json(status, **kw)))

    async def execution(self):
        async with self.sm() as s:
            return await s.get(InvoiceExecution, "e1")

    async def rows(self, model):
        async with self.sm() as s:
            return (await s.execute(select(model))).scalars().all()

    async def committed(self):
        async with self.sm() as s:
            return await spend_ledger.committed_cents(s, POLICY_ID, date(2026, 11, 5))

    async def sql(self, statement):
        async with self.sm() as s, s.begin():
            await s.execute(text(statement))


async def _seed_submitted(sm, status="PAYOUT_SUBMITTED"):
    async with sm() as s, s.begin():
        await seed_world(s)
        await seed_approved(s, status=status)
        s.add(
            Payout(
                payout_id="PAY-e1",
                execution_id="e1",
                sender_batch_id="AEGIS-e1",
                payout_batch_id=BATCH,
                payout_item_id=ITEM,
                receiver=PAYER_ID,
                amount_cents=48000,
            )
        )
        await spend_ledger.reserve(
            s,
            execution_id="e1",
            policy_id=POLICY_ID,
            vendor_id="v1",
            amount_cents=48000,
            now=NOW,
        )
        # submitted at NOW, so the fake clock and the poll window line up
        await s.execute(text("update invoice_executions set updated_at = :ts").bindparams(ts=NOW))


@pytest.fixture
async def env(pg_sessionmaker):
    await _seed_submitted(pg_sessionmaker)
    with respx.mock(assert_all_called=False) as router:
        yield Env(pg_sessionmaker, router)


async def test_success_records_payment_once_and_settles(env):
    env.set_item("SUCCESS")
    assert await env.rec.reconcile("e1") == "SETTLED"
    assert env.record.call_count == 1
    request = env.record.calls[0].request
    assert request.headers["paypal-request-id"] == "aegis-e1-recordpay"
    body = json.loads(request.content)
    assert body["amount"] == {"currency_code": "USD", "value": "480.00"}
    assert body["payment_date"] == "2026-11-05"
    assert (await env.execution()).status == "SETTLED"
    assert await env.committed() == 48000  # paid money stays counted against the cap


async def test_settled_execution_is_not_recorded_twice(env):
    env.set_item("SUCCESS")
    await env.rec.reconcile("e1")
    assert await env.rec.reconcile("e1") == "SETTLED"
    assert env.record.call_count == 1


async def test_short_pay_route_marks_notice_sent(env):
    await env.sql("update invoice_executions set route = 'SHORT_PAY'")
    env.set_item("SUCCESS")
    await env.rec.reconcile("e1")
    assert (await env.execution()).short_pay_balance == "NOTICE_SENT"


async def test_crash_after_succeeded_with_invoice_already_paid_skips_the_record_call(env):
    await env.sql("update invoice_executions set status = 'PAYOUT_SUCCEEDED'")
    paid = invoice_json("MARKED_AS_PAID", "480.00")
    env.invoice.mock(return_value=httpx.Response(200, json=paid))
    assert await env.rec.reconcile("e1") == "SETTLED"
    assert env.record.call_count == 0


async def test_crash_after_succeeded_with_unpaid_invoice_records_then_settles(env):
    await env.sql("update invoice_executions set status = 'PAYOUT_SUCCEEDED'")
    assert await env.rec.reconcile("e1") == "SETTLED"
    assert env.record.call_count == 1


async def test_record_payment_failure_is_manual_review_not_payout_failure(env):
    env.set_item("SUCCESS")
    env.record.mock(return_value=httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"}))
    assert await env.rec.reconcile("e1") == "NEEDS_MANUAL_REVIEW"
    assert await env.committed() == 48000
    assert (await env.rows(SettlementOutbox))[0].last_error == "RECORD_PAYMENT_FAILED"


@pytest.mark.parametrize("status", ["PENDING", "ONHOLD"])
async def test_pending_keeps_polling(env, status):
    env.set_item(status)
    assert await env.rec.reconcile("e1") == "PAYOUT_SUBMITTED"
    (payout,) = await env.rows(Payout)
    assert payout.item_status == status and payout.last_polled_at is not None
    assert env.record.call_count == 0


async def test_pending_past_the_window_goes_to_manual_review_keeping_reservation(
    pg_sessionmaker,
):
    await _seed_submitted(pg_sessionmaker)
    with respx.mock(assert_all_called=False) as router:
        late = Env(pg_sessionmaker, router, clock=lambda: NOW + timedelta(minutes=31))
        assert await late.rec.reconcile("e1") == "NEEDS_MANUAL_REVIEW"
        assert await late.committed() == 48000


async def test_unclaimed_freezes_and_keeps_reservation(env):
    env.set_item("UNCLAIMED")
    assert await env.rec.reconcile("e1") == "FROZEN"
    assert await env.committed() == 48000
    assert (await env.rows(SettlementOutbox))[0].last_error == "STALE_PAYEE"


@pytest.mark.parametrize("status", ["FAILED", "RETURNED", "BLOCKED", "DENIED"])
async def test_non_payment_releases_on_the_reserve_date(env, status):
    env.set_item(status)
    assert await env.rec.reconcile("e1") == "PAYOUT_FAILED"
    assert await env.committed() == 0
    entries = await env.rows(SpendLedger)
    assert sorted(e.kind for e in entries) == ["RELEASE", "RESERVE"]
    assert len({e.business_date for e in entries}) == 1
    assert env.record.call_count == 0


@pytest.mark.parametrize("status", ["REFUNDED", "REVERSED"])
async def test_reversal_after_success_is_manual_review_and_releases(env, status):
    env.set_item("SUCCESS")
    await env.rec.reconcile("e1")  # SETTLED
    env.set_item(status)
    assert await env.rec.reconcile("e1") == "NEEDS_MANUAL_REVIEW"
    assert await env.committed() == 0


async def test_late_pending_after_settled_is_ignored(env):
    env.set_item("SUCCESS")
    await env.rec.reconcile("e1")
    env.set_item("PENDING")
    assert await env.rec.reconcile("e1") == "SETTLED"
    assert (await env.execution()).status == "SETTLED"


@pytest.mark.parametrize(
    "bad", [{"receiver": "ATTACKER9999"}, {"cents": "480.01"}], ids=["receiver", "amount"]
)
async def test_item_that_differs_from_the_command_halts(env, bad):
    env.set_item("SUCCESS", **bad)
    assert await env.rec.reconcile("e1") == "NEEDS_MANUAL_REVIEW"
    (control,) = await env.rows(SettlementControl)
    assert control.halted is True
    assert env.record.call_count == 0
    assert await env.committed() == 48000


async def test_unknown_status_changes_nothing(env):
    env.set_item("MYSTERY")
    assert await env.rec.reconcile("e1") == "PAYOUT_SUBMITTED"
    assert await env.committed() == 48000


async def test_run_once_swallows_a_failing_item_and_reports_the_count(env):
    env.item.mock(return_value=httpx.Response(500, json={"name": "INTERNAL_SERVER_ERROR"}))
    assert await env.rec.run_once() == 1
    assert (await env.execution()).status == "PAYOUT_SUBMITTED"


async def test_run_once_settles_every_in_flight_execution(env):
    env.set_item("SUCCESS")
    assert await env.rec.run_once() == 1
    assert (await env.execution()).status == "SETTLED"
    assert await env.rec.run_once() == 0


async def test_only_one_reconciler_holds_the_advisory_lock(env):
    first_stop, second_stop = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(run_forever(env.rec, env.sm, first_stop, poll_s=0.05))
    await asyncio.sleep(0.3)  # let the first take the lock
    await asyncio.wait_for(run_forever(env.rec, env.sm, second_stop, poll_s=0.05), timeout=3)
    assert not first.done()  # the second exited at once; the first is still polling
    first_stop.set()
    await asyncio.wait_for(first, timeout=3)
