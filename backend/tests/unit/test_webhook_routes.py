"""P1-09: webhook routes + dedupe (R10, R24, R25) and the settlement-side consumer."""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models.payables import InvoiceExecution, Payout, WebhookEvent
from app.paypal.client import PayPalClient
from app.paypal.invoicing import Invoicing
from app.paypal.payouts import Payouts
from app.routers import webhooks
from app.settlement.reconciler import Reconciler
from app.settlement.webhook_consumer import WebhookConsumer
from app.vendor_sim.gateway import VendorGateway
from tests.seed import PAYER_ID, seed_approved, seed_world
from tests.webhook_signing import make_verifier, sign

pytestmark = pytest.mark.usefixtures("pg_template")
IDS = {"TREASURY": "treasury-id", "VENDOR": "vendor-id"}
TREASURY, VENDOR = "/api/webhooks/paypal/treasury", "/api/webhooks/paypal/vendor"
SIMULATOR = "/api/webhooks/paypal/simulator"
BASE = "https://api-m.sandbox.paypal.com"


def event(event_id="WH-1", event_type="PAYMENT.PAYOUTS-ITEM.SUCCEEDED", **resource):
    resource = resource or {"payout_item_id": "ITEM1"}
    body = {"id": event_id, "event_type": event_type, "resource": resource}
    return json.dumps(body).encode()


@pytest.fixture
async def hook(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        await seed_approved(s, status="PAYOUT_SUBMITTED")
        s.add(
            Payout(
                payout_id="PAY-e1",
                execution_id="e1",
                sender_batch_id="AEGIS-e1",
                payout_batch_id="BATCH1",
                payout_item_id="ITEM1",
                receiver=PAYER_ID,
                amount_cents=48000,
            )
        )
    verifier, _ = make_verifier()

    async def session_override():
        async with pg_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[webhooks.get_verifier] = lambda: verifier
    app.dependency_overrides[webhooks.get_webhook_ids] = lambda: IDS
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.sm = pg_sessionmaker
        yield client
    app.dependency_overrides.clear()


async def stored(client):
    async with client.sm() as s:
        return (await s.execute(select(WebhookEvent))).scalars().all()


async def post(client, route, raw, webhook_id="treasury-id", headers=None):
    return await client.post(route, content=raw, headers=headers or sign(raw, webhook_id))


async def test_verified_event_for_our_payout_is_stored_as_an_unprocessed_hint(hook):
    raw = event()
    assert (await post(hook, TREASURY, raw)).status_code == 204
    (row,) = await stored(hook)
    assert (row.event_id, row.app, row.resource_id) == ("WH-1", "TREASURY", "ITEM1")
    assert row.signature_verified and not row.simulated and not row.confirmed_by_get
    assert row.raw_body == raw and row.processed_at is None
    assert set(row.headers_json) == set(webhooks.KEPT_HEADERS)


async def test_batch_resource_identity_is_also_ours(hook):
    raw = event("WH-2", "PAYMENT.PAYOUTSBATCH.SUCCESS", batch_header={"payout_batch_id": "BATCH1"})
    assert (await post(hook, TREASURY, raw)).status_code == 204
    assert (await stored(hook))[0].resource_id == "BATCH1"


async def test_duplicate_event_id_is_acknowledged_and_stored_once(hook):
    raw = event()
    first = await post(hook, TREASURY, raw)
    again = await post(hook, TREASURY, raw)
    assert (first.status_code, again.status_code) == (204, 204)
    assert len(await stored(hook)) == 1


async def test_event_for_a_resource_we_did_not_create_is_acknowledged_but_not_stored(hook):
    assert (await post(hook, TREASURY, event(payout_item_id="STRANGER9"))).status_code == 204
    assert await stored(hook) == []


async def test_vendor_event_for_our_invoice_is_stored(hook):
    raw = event("WH-3", "INVOICING.INVOICE.PAID", invoice={"id": "INV2-ABC"})
    assert (await post(hook, VENDOR, raw, "vendor-id")).status_code == 204
    (row,) = await stored(hook)
    assert (row.app, row.resource_id) == ("VENDOR", "INV2-ABC")


async def test_vendor_event_for_an_unknown_invoice_is_not_stored(hook):
    raw = event("WH-3", "INVOICING.INVOICE.PAID", invoice={"id": "INV2-OTHER"})
    assert (await post(hook, VENDOR, raw, "vendor-id")).status_code == 204
    assert await stored(hook) == []


async def test_bad_signature_is_rejected_and_only_a_2kb_sample_is_kept(hook):
    raw = event(note="x" * 5000)
    headers = {**sign(raw, "treasury-id"), "paypal-transmission-sig": "AAAA"}
    assert (await post(hook, TREASURY, raw, headers=headers)).status_code == 400
    (row,) = await stored(hook)
    assert not row.signature_verified and row.event_type == "UNVERIFIED"
    assert len(row.raw_body) == webhooks.SAMPLE_BYTES and row.processed_at is not None


async def test_event_signed_for_the_other_app_is_rejected(hook):
    assert (await post(hook, TREASURY, event(), "vendor-id")).status_code == 400
    vendor_raw = event("WH-4", "INVOICING.INVOICE.PAID", id="INV2-ABC")
    assert (await post(hook, VENDOR, vendor_raw, "treasury-id")).status_code == 400


async def test_event_type_of_the_other_app_is_rejected_even_if_signed(hook):
    raw = event("WH-5", "INVOICING.INVOICE.PAID", invoice={"id": "INV2-ABC"})
    assert (await post(hook, TREASURY, raw)).status_code == 400
    (row,) = await stored(hook)
    assert row.event_type == "UNVERIFIED"


async def test_body_over_64kb_is_rejected_before_verification(hook):
    raw = b"x" * (webhooks.MAX_BODY_BYTES + 1)
    assert (await post(hook, TREASURY, raw)).status_code == 413
    assert await stored(hook) == []


async def test_unverified_duplicates_collapse_to_one_row(hook):
    for _ in range(3):
        assert (await hook.post(TREASURY, content=b"garbage")).status_code == 400
    assert len(await stored(hook)) == 1


async def test_simulator_route_is_a_404_unless_demo_is_enabled(hook):
    response = await post(hook, f"{SIMULATOR}/treasury", event(), "WEBHOOK_ID")
    assert response.status_code == 404
    assert await stored(hook) == []


async def test_simulated_event_is_stored_flagged_and_already_processed(hook, monkeypatch):
    monkeypatch.setenv("DEMO_ENABLED", "true")
    get_settings.cache_clear()
    raw = event("WH-SIM")
    assert (await post(hook, f"{SIMULATOR}/treasury", raw, "WEBHOOK_ID")).status_code == 204
    (row,) = await stored(hook)
    assert row.simulated and row.signature_verified and row.processed_at is not None


async def test_simulator_signature_is_not_valid_on_the_real_route(hook):
    assert (await post(hook, TREASURY, event(), "WEBHOOK_ID")).status_code == 400


class FakeReconciler:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def reconcile(self, execution_id):
        self.calls.append(execution_id)
        if self.fail:
            raise RuntimeError("boom")
        return "PAYOUT_SUBMITTED"


async def test_consumer_reconciles_treasury_hints_and_marks_them_processed(hook):
    await post(hook, TREASURY, event())
    fake = FakeReconciler()
    assert await WebhookConsumer(hook.sm, fake).run_once() == 1
    assert fake.calls == ["e1"]
    (row,) = await stored(hook)
    assert row.processed_at is not None and row.confirmed_by_get is True
    assert await WebhookConsumer(hook.sm, fake).run_once() == 0  # nothing left


async def test_consumer_ignores_vendor_simulated_and_unverified_events(hook, monkeypatch):
    monkeypatch.setenv("DEMO_ENABLED", "true")
    get_settings.cache_clear()
    await post(hook, VENDOR, event("WH-3", "INVOICING.INVOICE.PAID", id="INV2-ABC"), "vendor-id")
    await post(hook, f"{SIMULATOR}/treasury", event("WH-SIM"), "WEBHOOK_ID")
    await hook.post(TREASURY, content=b"garbage")
    fake = FakeReconciler()
    assert await WebhookConsumer(hook.sm, fake).run_once() == 1  # only the verified vendor hint
    assert fake.calls == []  # vendor events carry no money transition


async def test_consumer_leaves_an_event_unprocessed_when_reconcile_fails(hook):
    await post(hook, TREASURY, event())
    await WebhookConsumer(hook.sm, FakeReconciler(fail=True)).run_once()
    assert (await stored(hook))[0].processed_at is None


async def _no_sleep(_):
    return None


def _paypal(app_name):
    return PayPalClient(
        app=app_name, base_url=BASE, client_id="a", client_secret="b", sleep=_no_sleep
    )


async def test_a_success_webhook_is_only_a_hint_the_get_decides(hook):
    """R10: the event claims SUCCEEDED; PayPal's GET says PENDING, so nothing moves."""
    await post(hook, TREASURY, event())
    item = {
        "payout_item_id": "ITEM1",
        "transaction_status": "PENDING",
        "payout_item": {
            "recipient_type": "PAYPAL_ID",
            "receiver": PAYER_ID,
            "amount": {"value": "480.00", "currency": "USD"},
            "sender_item_id": "e1-1",
        },
    }
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/v1/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        )
        router.get(f"{BASE}/v1/payments/payouts-item/ITEM1").mock(
            return_value=httpx.Response(200, json=item)
        )
        record = router.post(url__regex=r".*/payments$")
        reconciler = Reconciler(
            hook.sm, Payouts(_paypal("TREASURY")), VendorGateway(Invoicing(_paypal("VENDOR")))
        )
        await WebhookConsumer(hook.sm, reconciler).run_once()
        assert not record.called
    async with hook.sm() as s:
        assert (await s.get(InvoiceExecution, "e1")).status == "PAYOUT_SUBMITTED"
