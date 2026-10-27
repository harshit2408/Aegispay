"""R19/R20 crash boundaries and the retained 401 halt bypass regression."""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.core import MandatePolicy, PurchaseOrder
from app.models.payables import InvoiceExecution, Payout, POLedger, SettlementOutbox, SpendLedger
from app.paypal.client import PayPalAuthError
from app.settlement import po_ledger, spend_ledger
from tests.seed import seed_approved, seed_world
from tests.unit.test_settlement_service import Env, batch_json, created_json, dup_error

pytestmark = pytest.mark.usefixtures("pg_template")


@pytest.fixture
async def env(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        await seed_approved(s)
    with respx.mock(assert_all_called=False) as router:
        yield Env(pg_sessionmaker, router)


async def test_halt_during_401_token_refresh_blocks_second_payout_post(env):
    access_token = env.service._payouts._client._access_token

    async def token_then_halt(*, force=False):
        token = await access_token(force=force)
        if force:
            await env.sql("update settlement_controls set halted = true")
        return token

    env.service._payouts._client._access_token = token_then_halt
    env.post.mock(side_effect=[httpx.Response(401), httpx.Response(201, json=created_json())])
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 1
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1


async def test_halt_during_initial_treasury_token_fetch_blocks_first_payout_post(env):
    access_token = env.service._payouts._client._access_token

    async def token_then_halt(*, force=False):
        token = await access_token(force=force)
        await env.sql("update settlement_controls set halted = true")
        return token

    env.service._payouts._client._access_token = token_then_halt
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 0
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1


async def test_auth_failure_after_write_ahead_goes_to_review_with_reservations(env, monkeypatch):
    async def failed_token():
        raise PayPalAuthError("Treasury OAuth unavailable")

    monkeypatch.setattr(env.service._payouts._client, "_fetch_token", failed_token)
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 0
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1


async def test_401_refresh_retry_has_same_command_and_full_recheck(env):
    env.post.mock(side_effect=[httpx.Response(401), httpx.Response(201, json=created_json())])
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert env.post.call_count == 2
    assert env.post.calls[0].request.content == env.post.calls[1].request.content
    assert env.invoice.call_count == 3


async def test_post_sees_committed_write_ahead_and_no_database_locks(env):
    async def provider(request):
        async with env.sm() as s, s.begin():
            execution = (
                await s.execute(select(InvoiceExecution).with_for_update(nowait=True))
            ).scalar_one()
            assert execution.status == "PAYOUT_SENDING"
            assert (await s.execute(select(Payout))).scalar_one().sender_batch_id == "AEGIS-e1"
            assert (await s.execute(select(SpendLedger))).scalar_one().amount_cents == 48000
            assert (await s.execute(select(POLedger))).scalar_one().amount_cents == 48000
            await s.execute(select(MandatePolicy).with_for_update(nowait=True))
            await s.execute(select(PurchaseOrder).with_for_update(nowait=True))
        return httpx.Response(201, json=created_json())

    env.post.mock(side_effect=provider)
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"


async def test_initial_recheck_failure_releases_any_approval_reservations(env):
    async with env.sm() as s, s.begin():
        await spend_ledger.reserve(
            s, execution_id="e1", policy_id="pol-1", vendor_id="v1", amount_cents=48000
        )
        await po_ledger.reserve(s, execution_id="e1", po_id="PO-1042", amount_cents=48000)
    await env.sql("update vendor_master set version=2")
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0
    assert sorted(e.kind for e in await env.rows(POLedger)) == ["RELEASE", "RESERVE"]
    assert sorted(e.kind for e in await env.rows(SpendLedger)) == ["RELEASE", "RESERVE"]


async def test_po_race_rolls_back_new_spend_reserve(env, monkeypatch):
    async def exhausted(*args, **kwargs):
        raise po_ledger.OverConsumption("PO-1042")

    monkeypatch.setattr(po_ledger, "reserve", exhausted)
    assert await env.service.execute("e1") == "FROZEN"
    assert await env.rows(SpendLedger) == await env.rows(POLedger) == []
    assert env.post.call_count == 0


@pytest.mark.parametrize("change", ["amount", "currency", "remaining"])
async def test_structured_invoice_amount_bounds_are_rechecked(env, change):
    from tests.unit.test_settlement_service import invoice_json

    invoice = invoice_json()
    if change == "amount":
        invoice["amount"]["value"] = "479.99"
    elif change == "currency":
        invoice["amount"]["currency_code"] = "EUR"
    else:
        invoice["due_amount"] = {"currency_code": "USD", "value": "479.99"}
    env.invoice.mock(return_value=httpx.Response(200, json=invoice))
    assert await env.service.execute("e1") == "FROZEN"
    assert env.post.call_count == 0
    assert await env.rows(SpendLedger) == await env.rows(POLedger) == []


@pytest.mark.parametrize("bad", ["/evil", None, []])
async def test_malformed_batch_response_keeps_reservations_for_review(env, bad):
    env.post.mock(return_value=httpx.Response(201, json={"batch_header": {"payout_batch_id": bad}}))
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 1
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("update vendor_master set version=2", "VENDOR_VERSION_CHANGED"),
        ("update purchase_orders set version=2", "PO_CHANGED"),
        ("update purchase_orders set status='CLOSED'", "PO_CLOSED"),
        ("update settlement_controls set halted=true", "HALTED"),
    ],
)
async def test_change_during_final_invoice_get_prevents_first_post(env, sql, reason):
    original = env.service._vendor.get_invoice
    calls = 0

    async def change_during_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        invoice = await original(*args, **kwargs)
        if calls == 2:  # after write-ahead; the final DB read must observe it
            await env.sql(sql)
        return invoice

    env.service._vendor.get_invoice = change_during_read
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert env.post.call_count == 0
    assert (await env.rows(SettlementOutbox))[0].last_error == reason
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1


class Crash(BaseException):
    """A process death must not be caught as an ordinary network error."""


async def test_crash_before_write_ahead_commit_rolls_back_both_reservations(env, monkeypatch):
    from app.settlement import transitions

    apply = transitions.apply

    async def die_before_commit(session, execution, to, **kwargs):
        await apply(session, execution, to, **kwargs)
        if to == "PAYOUT_SENDING":
            raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(transitions, "apply", die_before_commit)
        with pytest.raises(Crash):
            await env.service.execute("e1")
    assert await env.status() == "APPROVED_FOR_PAYMENT"
    assert await env.rows(SpendLedger) == await env.rows(POLedger) == await env.rows(Payout) == []
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert env.post.call_count == 1


@pytest.mark.parametrize("boundary", ["before_post", "after_post", "before_submit_commit"])
async def test_crash_after_write_ahead_recovers_one_frozen_payment(env, monkeypatch, boundary):
    processed = set()

    async def provider(request):
        key = json.loads(request.content)["sender_batch_header"]["sender_batch_id"]
        if key in processed:
            return httpx.Response(400, json=dup_error())
        processed.add(key)
        if boundary == "after_post":
            raise Crash()
        return httpx.Response(201, json=created_json())

    env.post.mock(side_effect=provider)

    async def die(*args, **kwargs):
        raise Crash()

    with monkeypatch.context() as patch:
        if boundary == "before_post":
            patch.setattr(env.service, "_post_and_submit", die)
        if boundary == "before_submit_commit":
            patch.setattr(env.service, "_submitted", die)
        with pytest.raises(Crash):
            await env.service.execute("e1")
    assert await env.status() == "PAYOUT_SENDING"
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1
    before = env.post.call_count
    assert await env.service.execute("e1") == "PAYOUT_SUBMITTED"
    assert env.post.call_count - before == 1
    assert processed == {"AEGIS-e1"}
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1
    assert len(await env.rows(Payout)) == 1


@pytest.mark.parametrize("mismatch", ["extra_item", "wrong_sender_item", "wrong_sender_batch"])
async def test_adoption_assertion_failure_halts_and_keeps_both_reservations(env, mismatch):
    body = batch_json()
    if mismatch == "extra_item":
        body["items"].append(body["items"][0])
    elif mismatch == "wrong_sender_item":
        body["items"][0]["payout_item"]["sender_item_id"] = "other-1"
    else:
        body["batch_header"]["sender_batch_header"]["sender_batch_id"] = "AEGIS-other"
    env.post.mock(return_value=httpx.Response(400, json=dup_error()))
    env.get_batch.mock(return_value=httpx.Response(200, json=body))
    assert await env.service.execute("e1") == "NEEDS_MANUAL_REVIEW"
    assert len(await env.rows(SpendLedger)) == len(await env.rows(POLedger)) == 1
    assert env.post.call_count == 1
    assert (await env.rows(InvoiceExecution))[0].row_version == 2
