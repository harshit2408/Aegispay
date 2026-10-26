"""P1-04: Treasury payouts client (PRD §9.3, R1, R5, R19)."""

import json

import httpx
import pytest
import respx

from app.paypal.client import PayPalClient, PayPalError
from app.paypal.payouts import (
    PayoutCommand,
    PayoutMismatch,
    Payouts,
    PayoutUnresolved,
)

BASE = "https://api-m.sandbox.paypal.com"
EX = "ex-1042"
BATCH = "BATCH123ABC"


async def _no_sleep(_: float) -> None:
    return None


def cmd(**kw) -> PayoutCommand:
    args = {
        "execution_id": EX,
        "receiver_payer_id": "PAYER12345678",
        "amount_cents": 48000,
        "note": "INV-1 / PO-1042",
    }
    return PayoutCommand(**{**args, **kw})


def batch_body(**over):
    item = {
        "recipient_type": "PAYPAL_ID",
        "receiver": "PAYER12345678",
        "amount": {"value": "480.00", "currency": "USD"},
        "sender_item_id": f"{EX}-1",
    }
    item.update(over.pop("item", {}))
    body = {
        "batch_header": {
            "payout_batch_id": BATCH,
            "batch_status": "PENDING",
            "sender_batch_header": {"sender_batch_id": f"AEGIS-{EX}"},
        },
        "items": [
            {"payout_item_id": "ITEM1", "transaction_status": "PENDING", "payout_item": item}
        ],
    }
    body.update(over)
    return body


def dup_error(batch=BATCH, base="https://api.sandbox.paypal.com"):
    return {
        "name": "USER_BUSINESS_ERROR",
        "details": [
            {
                "field": "SENDER_BATCH_ID",
                "issue": "Batch with given sender_batch_id already exists",
                "link": [
                    {"href": f"{base}/v1/payments/payouts/{batch}", "rel": "self", "method": "GET"}
                ],
            }
        ],
    }


@pytest.fixture
def payouts():
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/v1/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        )
        client = PayPalClient(
            app="TREASURY", base_url=BASE, client_id="a", client_secret="b", sleep=_no_sleep
        )
        yield Payouts(client), router


def test_command_derives_keys_from_execution_id():
    c = cmd()
    assert c.sender_batch_id == f"AEGIS-{EX}"
    assert c.sender_item_id == f"{EX}-1"
    assert c.request_id == f"aegis-{EX}-payout"


@pytest.mark.parametrize("bad", ["", "a b", "x/../y", "a" * 65, "é"])
def test_command_rejects_unsafe_execution_id(bad):
    with pytest.raises(ValueError):
        cmd(execution_id=bad)


@pytest.mark.parametrize("bad", ["", "a@b.com", "ab", "PAYER 1234", "x" * 33])
def test_command_rejects_non_payer_id_receiver(bad):
    with pytest.raises(ValueError):
        cmd(receiver_payer_id=bad)


@pytest.mark.parametrize("bad", [0, -5, 1.5, True])
def test_command_rejects_bad_amount(bad):
    with pytest.raises((ValueError, TypeError)):
        cmd(amount_cents=bad)


async def test_create_posts_frozen_body_with_request_id(payouts):
    p, router = payouts
    route = router.post(f"{BASE}/v1/payments/payouts").mock(
        return_value=httpx.Response(
            201, json={"batch_header": {"payout_batch_id": BATCH, "batch_status": "PENDING"}}
        )
    )
    ref = await p.create_or_adopt(cmd())
    assert ref.payout_batch_id == BATCH and ref.adopted is False
    req = route.calls[0].request
    assert req.headers["paypal-request-id"] == f"aegis-{EX}-payout"
    body = json.loads(req.content)
    assert body["sender_batch_header"]["sender_batch_id"] == f"AEGIS-{EX}"
    item = body["items"][0]
    assert item["recipient_type"] == "PAYPAL_ID"
    assert item["receiver"] == "PAYER12345678"
    assert item["amount"] == {"value": "480.00", "currency": "USD"}
    assert item["sender_item_id"] == f"{EX}-1"


async def test_duplicate_batch_is_adopted_after_full_get_match(payouts):
    p, router = payouts
    router.post(f"{BASE}/v1/payments/payouts").mock(
        return_value=httpx.Response(400, json=dup_error())
    )
    get = router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
        return_value=httpx.Response(200, json=batch_body())
    )
    ref = await p.create_or_adopt(cmd())
    assert ref.payout_batch_id == BATCH and ref.adopted is True
    assert ref.payout_item_id == "ITEM1"
    # GET goes to the configured base, never to the host inside the href
    assert get.calls[0].request.url.host == "api-m.sandbox.paypal.com"


@pytest.mark.parametrize(
    "item",
    [
        {"receiver": "OTHERPAYER999"},
        {"amount": {"value": "480.01", "currency": "USD"}},
        {"amount": {"value": "480.00", "currency": "EUR"}},
        {"recipient_type": "EMAIL"},
        {"sender_item_id": "other-1"},
    ],
)
async def test_adopt_mismatch_raises_with_found_batch_id(payouts, item):
    p, router = payouts
    router.post(f"{BASE}/v1/payments/payouts").mock(
        return_value=httpx.Response(400, json=dup_error())
    )
    router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
        return_value=httpx.Response(200, json=batch_body(item=item))
    )
    with pytest.raises(PayoutMismatch) as exc:
        await p.create_or_adopt(cmd())
    assert exc.value.payout_batch_id == BATCH


async def test_adopt_rejects_wrong_sender_batch_id_and_extra_items(payouts):
    p, router = payouts
    router.post(f"{BASE}/v1/payments/payouts").mock(
        return_value=httpx.Response(400, json=dup_error())
    )
    bad = batch_body()
    bad["batch_header"]["sender_batch_header"]["sender_batch_id"] = "AEGIS-other"
    router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
        return_value=httpx.Response(200, json=bad)
    )
    with pytest.raises(PayoutMismatch):
        await p.create_or_adopt(cmd())
    two = batch_body()
    two["items"].append(two["items"][0])
    router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
        return_value=httpx.Response(200, json=two)
    )
    with pytest.raises(PayoutMismatch):
        await p.create_or_adopt(cmd())


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.test/v1/payments/payouts/BATCH123ABC",
        "https://api.sandbox.paypal.com/v1/payments/payouts/BATCH123ABC/../x",
        "https://api.sandbox.paypal.com/v1/payments/payouts/batch_lower",
        "http://api.sandbox.paypal.com/v1/payments/payouts/BATCH123ABC",
    ],
)
async def test_duplicate_without_valid_link_is_unresolved(payouts, href):
    p, router = payouts
    err = dup_error()
    err["details"][0]["link"][0]["href"] = href
    router.post(f"{BASE}/v1/payments/payouts").mock(return_value=httpx.Response(400, json=err))
    get = router.get(url__regex=r".*/v1/payments/payouts/.*")
    with pytest.raises(PayoutUnresolved):
        await p.create_or_adopt(cmd())
    assert not get.called


async def test_other_errors_propagate(payouts):
    p, router = payouts
    router.post(f"{BASE}/v1/payments/payouts").mock(
        return_value=httpx.Response(400, json={"name": "VALIDATION_ERROR"})
    )
    with pytest.raises(PayPalError):
        await p.create_or_adopt(cmd())


async def test_get_item_and_batch_parse_status_and_cents(payouts):
    p, router = payouts
    router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
        return_value=httpx.Response(200, json=batch_body())
    )
    router.get(f"{BASE}/v1/payments/payouts-item/ITEM1").mock(
        return_value=httpx.Response(
            200,
            json={
                "payout_item_id": "ITEM1",
                "payout_batch_id": BATCH,
                "transaction_status": "SUCCESS",
                "payout_item": batch_body()["items"][0]["payout_item"],
            },
        )
    )
    batch = await p.get_batch(BATCH)
    assert batch.batch_status == "PENDING"
    assert batch.sender_batch_id == f"AEGIS-{EX}"
    assert batch.items[0].amount_cents == 48000
    item = await p.get_item("ITEM1")
    assert item.status == "SUCCESS" and item.payout_batch_id == BATCH
    assert item.matches(cmd())


async def test_unparseable_amount_in_get_is_a_mismatch_not_a_crash(payouts):
    p, router = payouts
    router.get(f"{BASE}/v1/payments/payouts/{BATCH}").mock(
        return_value=httpx.Response(
            200, json=batch_body(item={"amount": {"value": "480.005", "currency": "USD"}})
        )
    )
    batch = await p.get_batch(BATCH)
    assert not batch.matches(cmd())


async def test_cancel_item_uses_cancel_request_id(payouts):
    p, router = payouts
    route = router.post(f"{BASE}/v1/payments/payouts-item/ITEM1/cancel").mock(
        return_value=httpx.Response(200, json={})
    )
    await p.cancel_item("ITEM1", execution_id=EX)
    assert route.calls[0].request.headers["paypal-request-id"] == f"aegis-{EX}-cancel"


@pytest.mark.parametrize("bad", ["", "a/b", "ITEM1/../x", "item 1"])
async def test_ids_in_paths_are_validated(payouts, bad):
    p, _ = payouts
    with pytest.raises(ValueError):
        await p.get_batch(bad)
    with pytest.raises(ValueError):
        await p.get_item(bad)
