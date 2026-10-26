"""P1-09: raw-byte webhook self-verification (R10) and event identity parsing (SPIKE-8)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.paypal.webhooks import (
    LIVE_HOSTS,
    MAX_BODY_BYTES,
    WebhookRejected,
    parse_event,
    validate_cert_url,
)
from tests.webhook_signing import CERT_URL, NOW, OTHER_CA, leaf_pem, make_verifier, sign

RAW = (
    b'{ "id":"WH-1", "event_type":"PAYMENT.PAYOUTS-ITEM.SUCCEEDED",'
    b' "resource":{"payout_item_id":"ITEM1"} }'
)
WEBHOOK_ID = "treasury-id"


async def test_valid_signature_over_the_exact_bytes_verifies():
    verifier, requests = make_verifier()
    await verifier.verify(RAW, sign(RAW, WEBHOOK_ID), WEBHOOK_ID)
    assert len(requests) == 2  # signing certificate + pinned issuer


async def test_reserialized_body_fails_because_signature_covers_raw_bytes():
    verifier, _ = make_verifier()
    headers = sign(RAW, WEBHOOK_ID)
    pretty = json.dumps(json.loads(RAW)).encode()
    assert pretty != RAW
    with pytest.raises(WebhookRejected):
        await verifier.verify(pretty, headers, WEBHOOK_ID)


async def test_signature_for_another_webhook_id_fails():
    verifier, _ = make_verifier()
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, sign(RAW, "vendor-id"), WEBHOOK_ID)


async def test_empty_webhook_id_fails_closed():
    verifier, _ = make_verifier()
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, sign(RAW, ""), "")


@pytest.mark.parametrize("age", [timedelta(minutes=11), timedelta(minutes=-11)])
async def test_transmission_outside_ten_minutes_is_rejected(age):
    verifier, _ = make_verifier()
    headers = sign(RAW, WEBHOOK_ID, when=datetime.now(UTC) - age)
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, headers, WEBHOOK_ID)


async def test_other_signature_algorithms_are_rejected():
    verifier, _ = make_verifier()
    headers = {**sign(RAW, WEBHOOK_ID), "paypal-auth-algo": "SHA1withRSA"}
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, headers, WEBHOOK_ID)


@pytest.mark.parametrize("missing", ["paypal-transmission-id", "paypal-transmission-sig"])
async def test_missing_header_is_rejected(missing):
    verifier, _ = make_verifier()
    headers = sign(RAW, WEBHOOK_ID)
    del headers[missing]
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, headers, WEBHOOK_ID)


async def test_garbage_signature_is_rejected():
    verifier, _ = make_verifier()
    headers = {**sign(RAW, WEBHOOK_ID), "paypal-transmission-sig": "not base64!!"}
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, headers, WEBHOOK_ID)


async def test_body_over_64kb_is_rejected():
    verifier, _ = make_verifier()
    raw = b"x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(WebhookRejected):
        await verifier.verify(raw, sign(raw, WEBHOOK_ID), WEBHOOK_ID)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.sandbox.paypal.com/v1/notifications/certs/CERT-x",
        "https://api.sandbox.paypal.com.evil.test/v1/notifications/certs/CERT-x",
        "https://evil.test/v1/notifications/certs/CERT-x",
        "https://user@api.sandbox.paypal.com/v1/notifications/certs/CERT-x",
        "https://api.sandbox.paypal.com:8443/v1/notifications/certs/CERT-x",
        "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-x?next=http://evil.test",
        "https://api.sandbox.paypal.com/v1/other/CERT-x",
        "https://api.paypal.com/v1/notifications/certs/CERT-x",  # live host, sandbox verifier
    ],
)
async def test_cert_url_must_be_an_exact_https_paypal_sandbox_host(url):
    verifier, requests = make_verifier()
    headers = {**sign(RAW, WEBHOOK_ID), "paypal-cert-url": url}
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, headers, WEBHOOK_ID)
    assert requests == []  # nothing was fetched from a rejected URL


def test_live_hosts_are_accepted_by_a_live_configured_verifier():
    url = "https://api-m.paypal.com/v1/notifications/certs/CERT-x"
    assert validate_cert_url(url, LIVE_HOSTS) == url


async def test_certificate_from_an_untrusted_ca_is_rejected():
    verifier, _ = make_verifier(roots=[OTHER_CA])  # the signing CA is not trusted
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, sign(RAW, WEBHOOK_ID), WEBHOOK_ID)


async def test_expired_certificate_is_rejected():
    expired = leaf_pem(start=NOW - timedelta(days=3), end=NOW - timedelta(days=1))
    verifier, _ = make_verifier(pem=expired)
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, sign(RAW, WEBHOOK_ID), WEBHOOK_ID)


async def test_certificate_for_the_wrong_hostname_is_rejected():
    verifier, _ = make_verifier(pem=leaf_pem(host="evil.example"))
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, sign(RAW, WEBHOOK_ID), WEBHOOK_ID)


async def test_failed_certificate_download_is_rejected():
    verifier, _ = make_verifier(status=404)
    with pytest.raises(WebhookRejected):
        await verifier.verify(RAW, sign(RAW, WEBHOOK_ID), WEBHOOK_ID)


async def test_certificate_is_cached_by_url():
    verifier, requests = make_verifier()
    for _ in range(3):
        await verifier.verify(RAW, sign(RAW, WEBHOOK_ID), WEBHOOK_ID)
    assert len(requests) == 2
    assert CERT_URL in {str(r.url) for r in requests}


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        ({"payout_item_id": "ITEM1"}, "ITEM1"),
        ({"payout_batch_id": "BATCH1"}, "BATCH1"),
        ({"batch_header": {"payout_batch_id": "BATCH2"}}, "BATCH2"),
    ],
)
def test_treasury_resource_identity(resource, expected):
    raw = json.dumps(
        {"id": "WH-1", "event_type": "PAYMENT.PAYOUTSBATCH.SUCCESS", "resource": resource}
    ).encode()
    assert parse_event(raw, "TREASURY").resource_id == expected


@pytest.mark.parametrize(
    "resource", [{"invoice": {"id": "INV2-X"}}, {"id": "INV2-X"}], ids=["real", "simulator"]
)
def test_vendor_invoice_identity_real_and_simulator_shapes(resource):
    raw = json.dumps(
        {"id": "WH-2", "event_type": "INVOICING.INVOICE.PAID", "resource": resource}
    ).encode()
    assert parse_event(raw, "VENDOR").resource_id == "INV2-X"


@pytest.mark.parametrize(
    ("event_type", "app"),
    [
        ("INVOICING.INVOICE.PAID", "TREASURY"),
        ("PAYMENT.PAYOUTSBATCH.SUCCESS", "VENDOR"),
        ("PAYMENT.PAYOUTSFOO.SUCCESS", "TREASURY"),
    ],
)
def test_event_type_must_belong_to_the_route_app(event_type, app):
    raw = json.dumps({"id": "WH-3", "event_type": event_type, "resource": {"id": "X1"}}).encode()
    with pytest.raises(WebhookRejected):
        parse_event(raw, app)


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b'{"id": "WH", "event_type": "PAYMENT.PAYOUTS-ITEM.X"}',
        b'{"id": "WH", "event_type": "PAYMENT.PAYOUTS-ITEM.X", "resource": {}}',
        b'{"id": "bad id", "event_type": "PAYMENT.PAYOUTS-ITEM.X", "resource": {"id": "A"}}',
        b'{"id": "WH", "event_type": "PAYMENT.PAYOUTS-ITEM.X", "resource": {"id": "../x"}}',
        b'{"id": 5, "event_type": "PAYMENT.PAYOUTS-ITEM.X", "resource": {"id": "A"}}',
    ],
)
def test_malformed_events_are_rejected(raw):
    with pytest.raises(WebhookRejected):
        parse_event(raw, "TREASURY")
