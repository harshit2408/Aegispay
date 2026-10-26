import base64
import importlib.util
import json
import sys
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from hypothesis import given
from hypothesis import strategies as st

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/spikes/spike8_webhooks.py"
SPEC = importlib.util.spec_from_file_location("spike8_webhooks", SCRIPT)
spike = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = spike
SPEC.loader.exec_module(spike)
CERT_URL = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-example"
NOW = datetime.now(UTC)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "offline-test")])
HOST = "messageverificationcerts.sandbox.paypal.com"


def key_usage(ca=False):
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=True,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=None,
        decipher_only=None,
    )


CA = (
    x509.CertificateBuilder()
    .subject_name(NAME)
    .issuer_name(NAME)
    .public_key(CA_KEY.public_key())
    .serial_number(100)
    .not_valid_before(NOW - timedelta(days=10))
    .not_valid_after(NOW + timedelta(days=10))
    .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
    .add_extension(key_usage(True), critical=True)
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(CA_KEY.public_key()), critical=False)
    .sign(CA_KEY, hashes.SHA256())
)


def certificate(start=NOW - timedelta(days=1), end=NOW + timedelta(days=1), host=HOST):
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .issuer_name(NAME)
        .public_key(KEY.public_key())
        .serial_number(1)
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(key_usage(), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(CA_KEY.public_key()), critical=False
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(CA_KEY, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )


def signed(raw, webhook_id="treasury-id", when=None):
    timestamp = (when or datetime.now(UTC)).isoformat()
    message = f"delivery-1|{timestamp}|{webhook_id}|{zlib.crc32(raw) & 0xFFFFFFFF}"
    signature = KEY.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return {
        "paypal-transmission-id": "delivery-1",
        "paypal-transmission-time": timestamp,
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": CERT_URL,
        "paypal-transmission-sig": base64.b64encode(signature).decode(),
    }


RAW = (
    b'{ "id":"WH-test", "event_type":"PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "resource":{"id":"ITEM-1"}}'
)


async def deliver(
    tmp_path,
    raw=RAW,
    headers=None,
    route="/api/webhooks/paypal/treasury",
    pem=None,
    download_status=200,
    roots=None,
):
    requests = []

    def fetch(request):
        requests.append(request)
        if str(request.url) == spike.ISSUER_URL:
            return httpx.Response(200, content=CA.public_bytes(serialization.Encoding.DER))
        return httpx.Response(
            download_status,
            content=pem or certificate(),
            headers={"Location": "http://127.0.0.1/secret"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(fetch)) as cert_client:
        evidence = tmp_path / "evidence.jsonl"
        app = spike.create_app(
            {"treasury": "treasury-id", "vendor": "vendor-id"},
            evidence,
            client=cert_client,
            trust_roots=[CA] if roots is None else roots,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as receiver:
            response = await receiver.post(route, content=raw, headers=headers or signed(raw))
    rows = (
        [json.loads(row) for row in evidence.read_text().splitlines()] if evidence.exists() else []
    )
    return response, rows, requests


@pytest.mark.asyncio
async def test_valid_raw_signature_writes_only_compact_evidence(tmp_path):
    response, rows, requests = await deliver(tmp_path)
    assert response.status_code == 204
    assert rows[0]["verified"] is True and rows[0]["simulated"] is False
    assert rows[0]["app"] == "treasury" and rows[0]["resource_id"] == "ITEM-1"
    assert len(requests) == 2
    assert rows[0]["certificate_chain_validated"] is True
    assert "resource" not in rows[0] and "headers" not in rows[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", [{"payout_item_id": "ITEM-1"}, {"payout_batch_id": "BATCH-1"}])
async def test_paypal_payout_resources_without_generic_id(tmp_path, resource):
    event = json.loads(RAW)
    event["resource"] = resource
    raw = json.dumps(event).encode()
    response, rows, _ = await deliver(tmp_path, raw=raw)
    assert response.status_code == 204
    assert rows[0]["resource_id"] in {"ITEM-1", "BATCH-1"}


@pytest.mark.asyncio
async def test_vendor_invoice_event_wraps_invoice_in_resource(tmp_path):
    raw = json.dumps(
        {
            "id": "WH-invoice",
            "event_type": "INVOICING.INVOICE.PAID",
            "resource": {"invoice": {"id": "INV2-test"}},
        }
    ).encode()
    response, rows, _ = await deliver(
        tmp_path, raw=raw, headers=signed(raw, "vendor-id"), route="/api/webhooks/paypal/vendor"
    )
    assert response.status_code == 204 and rows[0]["resource_id"] == "INV2-test"


@pytest.mark.asyncio
async def test_simulator_signature_is_separate_from_real_routes(tmp_path):
    headers = signed(RAW, "WEBHOOK_ID")
    response, rows, _ = await deliver(tmp_path, headers=headers)
    assert response.status_code == 400 and rows == []
    response, rows, _ = await deliver(tmp_path, headers=headers, route="/simulator/treasury")
    assert response.status_code == 204 and rows[0]["simulated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["wrong-app", "reserialized", "stale", "future", "algo", "missing", "bad-base64", "no-tz"],
)
async def test_invalid_transmissions_fail_closed(tmp_path, change):
    headers = signed(RAW)
    raw = RAW
    if change == "wrong-app":
        headers = signed(RAW, "vendor-id")
    elif change == "reserialized":
        raw = json.dumps(json.loads(RAW)).encode()
    elif change in {"stale", "future"}:
        delta = timedelta(hours=-1 if change == "stale" else 1)
        headers = signed(RAW, when=datetime.now(UTC) + delta)
    elif change == "algo":
        headers["paypal-auth-algo"] = "SHA1withRSA"
    elif change == "missing":
        del headers["paypal-transmission-time"]
    elif change == "bad-base64":
        headers["paypal-transmission-sig"] = "!!!"
    else:
        headers["paypal-transmission-time"] = "2026-10-25T12:00:00"
    response, rows, _ = await deliver(tmp_path, raw=raw, headers=headers)
    assert response.status_code == 400 and rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-3, 3])
async def test_expired_or_not_yet_valid_certificate_rejected(tmp_path, offset):
    start = NOW + timedelta(days=offset)
    response, rows, _ = await deliver(tmp_path, pem=certificate(start, start + timedelta(days=1)))
    assert response.status_code == 400 and rows == []


@pytest.mark.asyncio
async def test_streamed_body_cap(tmp_path):
    response, rows, requests = await deliver(tmp_path, raw=b"x" * (65536 + 1))
    assert response.status_code == 413 and rows == [] and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 404, 500])
async def test_certificate_redirects_and_errors_are_never_followed(tmp_path, status):
    response, rows, requests = await deliver(tmp_path, download_status=status)
    assert response.status_code == 400 and rows == []
    assert len(requests) == 1 and str(requests[0].url) == CERT_URL


@pytest.mark.asyncio
async def test_untrusted_signing_chain_is_rejected(tmp_path):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_ca = (
        x509.CertificateBuilder()
        .subject_name(NAME)
        .issuer_name(NAME)
        .public_key(other_key.public_key())
        .serial_number(200)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(key_usage(True), critical=True)
        .sign(other_key, hashes.SHA256())
    )
    response, rows, _ = await deliver(tmp_path, roots=[other_ca])
    assert response.status_code == 400 and rows == []


@pytest.mark.asyncio
async def test_certificate_body_cap(tmp_path):
    response, rows, requests = await deliver(tmp_path, pem=b"x" * 65537)
    assert response.status_code == 400 and rows == [] and len(requests) == 1


@pytest.mark.asyncio
async def test_wrong_signing_hostname_is_rejected(tmp_path):
    response, rows, _ = await deliver(tmp_path, pem=certificate(host="other.example"))
    assert response.status_code == 400 and rows == []


@pytest.mark.asyncio
async def test_untrusted_certificate_url_is_rejected_before_network(tmp_path):
    headers = signed(RAW)
    headers["paypal-cert-url"] = "https://api.paypal.com.evil.test/v1/notifications/certs/CERT-x"
    response, rows, requests = await deliver(tmp_path, headers=headers)
    assert response.status_code == 400 and rows == [] and requests == []


@pytest.mark.asyncio
async def test_certificate_cache_avoids_repeat_downloads():
    downloads = []

    def fetch(request):
        downloads.append(str(request.url))
        raw = (
            CA.public_bytes(serialization.Encoding.DER)
            if str(request.url) == spike.ISSUER_URL
            else certificate()
        )
        return httpx.Response(200, content=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fetch)) as client:
        verifier = spike.WebhookVerifier(client, [CA])
        for _ in range(2):
            result = await verifier.verify(RAW, signed(RAW), "treasury-id")
            assert result["certificate_chain_validated"] is True
    assert downloads == [CERT_URL, spike.ISSUER_URL]


@pytest.mark.asyncio
async def test_certificate_cache_is_bounded_and_expiry_forces_refetch():
    downloads = []

    def fetch(request):
        downloads.append(str(request.url))
        data = (
            CA.public_bytes(serialization.Encoding.DER)
            if str(request.url) == spike.ISSUER_URL
            else certificate()
        )
        return httpx.Response(200, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fetch)) as client:
        verifier = spike.WebhookVerifier(client, [CA])
        for index in range(17):
            headers = signed(RAW)
            headers["paypal-cert-url"] = f"{CERT_URL}-{index}"
            await verifier.verify(RAW, headers, "treasury-id")
        assert len(verifier.cache) == 16 and f"{CERT_URL}-0" not in verifier.cache
        url = f"{CERT_URL}-16"
        verifier.cache[url] = verifier.cache[url][0], NOW - timedelta(seconds=1)
        count = len(downloads)
        await verifier.verify(RAW, headers, "treasury-id")
        assert len(downloads) == count + 1


@pytest.mark.parametrize(
    "url",
    [
        "http://api.paypal.com/v1/notifications/certs/CERT-x",
        "https://api.paypal.com.evil.test/v1/notifications/certs/CERT-x",
        "https://api.paypal.com@127.0.0.1/v1/notifications/certs/CERT-x",
        "https://api.paypal.com:444/v1/notifications/certs/CERT-x",
        "https://api.paypal.com/v1/notifications/certs/CERT-x?url=http://localhost",
        "https://api.paypal.com/v1/notifications/certs/CERT-x#fragment",
        "https://api.paypal.com/anything/CERT-x",
        "https://127.0.0.1/v1/notifications/certs/CERT-x",
    ],
)
def test_certificate_url_allowlist(url):
    with pytest.raises(ValueError):
        spike.validate_cert_url(url)


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=40))
def test_property_paypal_suffix_is_never_an_allowed_host(suffix):
    with pytest.raises(ValueError):
        spike.validate_cert_url(f"https://api.paypal.com.{suffix}/v1/notifications/certs/CERT-x")
