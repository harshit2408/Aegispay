"""P1-07: SettlementService.execute (R1, R5, R19, R20) on real Postgres with mocked PayPal."""

import inspect
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select, text

from app.models.core import GoodsReceipt
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
from app.settlement.service import NoCommand, SettlementService
from app.vendor_sim.gateway import VendorReader
from tests.seed import PAYER_ID, seed_approved, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")
BASE = "https://api-m.sandbox.paypal.com"
BATCH = "BATCH123ABC"


async def _no_sleep(_: float) -> None:
    return None


def batch_json(receiver=PAYER_ID, cents="480.00", eid="e1"):
    return {
        "batch_header": {
            "payout_batch_id": BATCH,
            "batch_status": "PENDING",
            "sender_batch_header": {"sender_batch_id": f"AEGIS-{eid}"},
        },
        "items": [
            {
                "payout_item_id": "ITEM1",
                "transaction_status": "PENDING",
                "payout_item": {
                    "recipient_type": "PAYPAL_ID",
                    "receiver": receiver,
                    "amount": {"value": cents, "currency": "USD"},
                    "sender_item_id": f"{eid}-1",
                },
            }
        ],
    }


def invoice_json(status="SENT"):
    return {
        "id": "INV2-ABC",
        "status": status,
        "detail": {"invoice_number": "N", "reference": "PO-1042", "currency_code": "USD"},
        "items": [],
        "amount": {"currency_code": "USD", "value": "480.00"},
    }


def created_json():
    return {"batch_header": {"payout_batch_id": BATCH, "batch_status": "PENDING"}}


def dup_error():
    href = f"https://api.sandbox.paypal.com/v1/payments/payouts/{BATCH}"
    return {
        "name": "USER_BUSINESS_ERROR",
        "details": [{"field": "SENDER_BATCH_ID", "link": [{"href": href, "rel": "self"}]}],
    }


def paypal_client(app):
    return PayPalClient(app=app, base_url=BASE, client_id="a", client_secret="b", sleep=_no_sleep)


class Env:
    def __init__(self, sm, router):
        self.sm = sm
        self.service = SettlementService(
            sm,
            Payouts(paypal_client("TREASURY")),
            VendorReader(Invoicing(paypal_client("VENDOR"))),
        )
        router.post(f"{BASE}/v1/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        )
        self.invoice = router.get(f"{BASE}/v2/invoicing/invoices/INV2-ABC").mock(
            return_value=httpx.Response(200, json=invoice_json())
        )
        self.post = router.post(f"{BASE}/v1/payments/payouts").mock(
            return_value=httpx.Response(201, json=created_json())
        )
        self.get_batch = router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
            return_value=httpx.Response(200, json=batch_json())
        )

    async def rows(self, model, **where):
        async with self.sm() as s:
            query = select(model)
            for key, value in where.items():
                query = query.where(getattr(model, key) == value)
            return (await s.execute(query)).scalars().all()

    async def status(self, execution_id="e1"):
        return (await self.rows(InvoiceExecution, execution_id=execution_id))[0].status

    async def sql(self, statements):
        async with self.sm() as s, s.begin():
            for statement in statements.split(";"):
                await s.execute(text(statement))


@pytest.fixture
async def env(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        await seed_approved(s)
    with respx.mock(assert_all_called=False) as router:
        yield Env(pg_sessionmaker, router)


async def test_happy_path_moves_to_submitted_with_frozen_values(env):
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert await env.status() == "PAYOUT_SUBMITTED"
    request = env.post.calls[0].request
    body = json.loads(request.content)
    assert body["items"][0]["receiver"] == PAYER_ID
    assert body["items"][0]["amount"]["value"] == "480.00"
    assert request.headers["paypal-request-id"] == "aegis-e1-payout"
    (payout,) = await env.rows(Payout)
    assert (payout.payout_batch_id, payout.payout_item_id) == (BATCH, "ITEM1")
    assert (payout.paypal_receiver, payout.paypal_amount_cents) == (PAYER_ID, 48000)
    assert payout.sender_batch_id == "AEGIS-e1"
    (entry,) = await env.rows(SpendLedger)
    assert (entry.kind, entry.amount_cents) == ("RESERVE", 48000)
    (outbox,) = await env.rows(SettlementOutbox)
    assert outbox.status == "DONE"


def test_execute_is_the_only_public_method_and_takes_only_an_execution_id():
    public = [n for n, _ in inspect.getmembers(SettlementService) if not n.startswith("_")]
    assert public == ["execute"]
    assert list(inspect.signature(SettlementService.execute).parameters) == ["self", "execution_id"]


async def test_second_execute_does_not_pay_twice(env):
    await env.service.execute("e1")
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert env.post.call_count == 1
    assert len(await env.rows(SpendLedger)) == 1


async def test_unknown_execution_raises(env):
    with pytest.raises(NoCommand):
        await env.service.execute("nope")


# Bypasses the R5/R24 triggers to prove the R20 recheck is still a second line of defence
BYPASS = "set local session_replication_role = replica; "


@pytest.mark.parametrize(
    ("break_it", "reason"),
    [
        ("update vendor_master set version = 2", "VENDOR_VERSION_CHANGED"),
        ("update vendor_master set trust_status = 'COOLING'", "VENDOR_NOT_VERIFIED"),
        ("update vendor_master set paypal_payer_id = 'OTHER99999'", "PAYEE_CHANGED"),
        ("update purchase_orders set version = 2", "PO_CHANGED"),
        ("update settlement_controls set halted = true", "HALTED"),
        (BYPASS + "update invoice_executions set approved_cents = 49000", "AMOUNT_CHANGED"),
        (BYPASS + "update invoice_executions set replayed = true", "REPLAYED_EXECUTION"),
        ("update invoice_executions set grn_id = null", "NO_GRN"),
        ("update invoice_executions set vendor_id = null", "NO_VENDOR"),
    ],
)
async def test_r20_recheck_failures_freeze_before_any_reservation_or_post(env, break_it, reason):
    await env.sql(break_it)
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0
    assert await env.rows(SpendLedger) == []
    assert await env.rows(Payout) == []
    (outbox,) = await env.rows(SettlementOutbox)
    assert (outbox.status, outbox.last_error) == ("FAILED", reason)


async def test_superseded_grn_freezes(env):
    async with env.sm() as s, s.begin():
        s.add(
            GoodsReceipt(
                grn_id="grn-2",
                po_id="PO-1042",
                supersedes_grn_id="grn-1",
                received_qty=90,
                accepted_qty=90,
                rejected_qty=0,
                recorded_by="op-ctl",
                source="SEEDED",
                received_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0


@pytest.mark.parametrize("status", ["CANCELLED", "PAID", "DRAFT"])
async def test_invoice_not_open_freezes(env, status):
    env.invoice.mock(return_value=httpx.Response(200, json=invoice_json(status)))
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0


async def test_over_cap_freezes_without_reserving_or_posting(env):
    await env.sql("update mandate_policies set daily_cap_cents = 47999")
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0
    assert await env.rows(SpendLedger) == []
    assert (await env.rows(SettlementOutbox))[0].last_error == "OVER_CAP"


async def test_duplicate_batch_is_adopted(env):
    env.post.mock(return_value=httpx.Response(400, json=dup_error()))
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    (payout,) = await env.rows(Payout)
    assert payout.payout_batch_id == BATCH


@pytest.mark.parametrize(
    "bad",
    [batch_json(receiver="ATTACKER9999"), batch_json(cents="480.01"), batch_json(eid="other")],
)
async def test_response_mismatch_halts_and_keeps_reservation(env, bad):
    env.get_batch.mock(return_value=httpx.Response(200, json=bad))
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    (control,) = await env.rows(SettlementControl)
    assert control.halted is True
    assert len(await env.rows(SpendLedger)) == 1  # reserved, never released (R8/R19)
    (payout,) = await env.rows(Payout)
    assert payout.payout_batch_id == BATCH  # the found batch is persisted for the human


async def test_adopted_batch_with_other_receiver_halts(env):
    env.post.mock(return_value=httpx.Response(400, json=dup_error()))
    env.get_batch.mock(return_value=httpx.Response(200, json=batch_json(receiver="ATTACKER9999")))
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    (control,) = await env.rows(SettlementControl)
    assert control.halted is True


async def test_post_that_never_answers_goes_to_manual_review_keeping_reservation(env):
    env.post.mock(side_effect=httpx.ReadTimeout("t"))
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 3
    assert len(await env.rows(SpendLedger)) == 1
    (payout,) = await env.rows(Payout)
    assert payout.payout_batch_id is None
    assert (await env.rows(SettlementOutbox))[0].last_error == "UNRESOLVED:ReadTimeout"


async def _crash_after_write_ahead(env, age=timedelta(minutes=1)):
    """Leave the DB as a crash right after the write-ahead commit, before the POST answered."""
    env.post.mock(side_effect=httpx.ReadTimeout("t"))
    await env.service.execute("e1")  # reserves + inserts the payouts row, then ends unresolved
    seconds = int(age.total_seconds())
    await env.sql("update invoice_executions set status='PAYOUT_SENDING' where execution_id='e1'")
    await env.sql(f"update payouts set created_at = now() - interval '{seconds} seconds'")
    env.post.mock(side_effect=None, return_value=httpx.Response(201, json=created_json()))
    env.baseline = env.post.call_count


async def test_recovery_reposts_with_same_keys_within_ttl(env):
    await _crash_after_write_ahead(env)
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    request = env.post.calls[-1].request
    assert request.headers["paypal-request-id"] == "aegis-e1-payout"
    assert json.loads(request.content)["sender_batch_header"]["sender_batch_id"] == "AEGIS-e1"
    assert len(await env.rows(SpendLedger)) == 1  # no second reservation


async def test_recovery_past_ttl_goes_to_manual_review_without_posting(env):
    await _crash_after_write_ahead(env, age=timedelta(minutes=31))
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == env.baseline
    assert len(await env.rows(SpendLedger)) == 1


async def test_recovery_while_halted_does_not_post(env):
    await _crash_after_write_ahead(env)
    await env.sql("update settlement_controls set halted = true")
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == env.baseline


async def test_recovery_with_known_batch_only_verifies(env):
    await _crash_after_write_ahead(env)
    await env.sql(f"update payouts set payout_batch_id = '{BATCH}'")
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert env.post.call_count == env.baseline


async def test_no_active_mandate_freezes_instead_of_retrying_forever(env):
    await env.sql("update mandate_policies set is_active = false")
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0
    assert (await env.rows(SettlementOutbox))[0].last_error == "NO_ACTIVE_MANDATE"


async def test_halt_that_lands_after_write_ahead_stops_the_post(env, monkeypatch):
    async def halted(_session):
        return True

    monkeypatch.setattr("app.settlement.halt.is_halted", halted)  # only the pre-POST check
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 0
    assert len(await env.rows(SpendLedger)) == 1  # reservation kept
    assert (await env.rows(SettlementOutbox))[0].last_error == "HALTED_BEFORE_POST"


async def test_lease_overlap_second_submit_is_a_no_op(env):
    await env.service.execute("e1")
    await env.sql("update invoice_executions set status = $$PAYOUT_SENDING$$")
    # a second worker finishing the same payout must not raise or double-record
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert len(await env.rows(SpendLedger)) == 1
