"""Offline PayPal webhook signing: throwaway CA, leaf for the PayPal cert host, signed headers."""

import base64
import zlib
from datetime import UTC, datetime, timedelta

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.paypal.webhooks import ISSUER_URL, SANDBOX_HOSTS, WebhookVerifier

CERT_URL = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-example"
HOST = "messageverificationcerts.sandbox.paypal.com"
NOW = datetime.now(UTC)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CA_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "offline-test-ca")])


def _usage(ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=True,
        data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca,
        encipher_only=None, decipher_only=None,
    )  # fmt: skip


def _ca(key, name) -> x509.Certificate:
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(100)
        .not_valid_before(NOW - timedelta(days=10))
        .not_valid_after(NOW + timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(_usage(True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )


CA = _ca(CA_KEY, CA_NAME)
OTHER_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unrelated-ca")])
OTHER_CA = _ca(rsa.generate_private_key(public_exponent=65537, key_size=2048), OTHER_NAME)


def leaf_pem(start=None, end=None, host=HOST) -> bytes:
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .issuer_name(CA_NAME)
        .public_key(KEY.public_key())
        .serial_number(1)
        .not_valid_before(start or NOW - timedelta(days=1))
        .not_valid_after(end or NOW + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_usage(False), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(CA_KEY.public_key()), critical=False
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    )
    return builder.sign(CA_KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)


def sign(raw: bytes, webhook_id: str, when=None, transmission_id="delivery-1") -> dict:
    timestamp = (when or datetime.now(UTC)).isoformat()
    message = f"{transmission_id}|{timestamp}|{webhook_id}|{zlib.crc32(raw) & 0xFFFFFFFF}"
    signature = KEY.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return {
        "paypal-transmission-id": transmission_id,
        "paypal-transmission-time": timestamp,
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": CERT_URL,
        "paypal-transmission-sig": base64.b64encode(signature).decode(),
    }


def make_verifier(pem=None, status=200, roots=None, hosts=SANDBOX_HOSTS):
    """A verifier whose certificate downloads are answered offline. Returns (verifier, requests)."""
    requests: list[httpx.Request] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == ISSUER_URL:
            return httpx.Response(200, content=CA.public_bytes(serialization.Encoding.DER))
        return httpx.Response(status, content=pem or leaf_pem())

    client = httpx.AsyncClient(transport=httpx.MockTransport(fetch))
    trust = [CA] if roots is None else roots
    return WebhookVerifier(client, allowed_hosts=hosts, trust_roots=trust), requests
