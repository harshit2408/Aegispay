"""PayPal webhook self-verification from the raw request bytes (PRD §9.4, R10).

Signed message: `<transmission_id>|<transmission_time>|<webhook_id>|<crc32(raw) as unsigned int>`,
SHA256withRSA only. The signing certificate is fetched only over HTTPS from an exact PayPal host
allowlist, its chain and validity are checked against the system roots (intermediate pinned to the
measured issuer, SPIKE-8), and it is cached by URL. A verified webhook is a hint: this module never
touches money state (R10).
"""

import base64
import binascii
import json
import re
import ssl
import zlib
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

MAX_BODY_BYTES = 64 * 1024  # R10
MAX_CERT_BYTES = 64 * 1024
MAX_AGE_S = 600  # R10: transmissions older than 10 min are rejected
SIGNATURE_ALGO = "SHA256withRSA"
CERT_CACHE_SIZE = 16
CERT_CACHE_TTL = timedelta(hours=1)
SANDBOX_HOSTS = frozenset({"api.sandbox.paypal.com", "api-m.sandbox.paypal.com"})
LIVE_HOSTS = frozenset({"api.paypal.com", "api-m.paypal.com"})
# Measured in SPIKE-8. A CA rotation fails closed until this is updated and the spike repeated.
ISSUER_URL = "https://cacerts.digicert.com/DigiCertEVRSACAG2.crt"
SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
CERT_PATH = re.compile(r"/v1/notifications/certs/CERT-[A-Za-z0-9-]+")
PREFIXES = {
    "TREASURY": ("PAYMENT.PAYOUTSBATCH.", "PAYMENT.PAYOUTS-ITEM."),
    "VENDOR": ("INVOICING.INVOICE.",),
}
VERIFY_ERRORS = (
    KeyError,
    ValueError,
    TypeError,
    AttributeError,
    InvalidSignature,
    VerificationError,
    binascii.Error,
    httpx.HTTPError,
)


class WebhookRejected(Exception):
    """The webhook failed verification or validation. The message is a fixed, non-sensitive code."""


@dataclass(frozen=True)
class WebhookEvent:
    event_id: str
    event_type: str
    resource_id: str
    resource: dict


def validate_cert_url(url: str, allowed_hosts: frozenset[str]) -> str:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in allowed_hosts
            and parsed.netloc == parsed.hostname
            and not parsed.query
            and not parsed.fragment
            and CERT_PATH.fullmatch(parsed.path)
        )
    except ValueError:
        valid = False
    if not valid:
        raise WebhookRejected("certificate url rejected")
    return url


def system_trust_roots() -> list[x509.Certificate]:
    ctx = ssl.create_default_context()
    return [x509.load_der_x509_certificate(raw) for raw in ctx.get_ca_certs(binary_form=True)]


class WebhookVerifier:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        allowed_hosts: frozenset[str] = SANDBOX_HOSTS | LIVE_HOSTS,
        trust_roots: list[x509.Certificate] | None = None,
        issuer_url: str = ISSUER_URL,
    ) -> None:
        self._client = client
        self._hosts = allowed_hosts
        self._issuer_url = issuer_url
        self._trust = Store(trust_roots if trust_roots is not None else system_trust_roots())
        self._issuer: x509.Certificate | None = None
        self._cache: OrderedDict[str, tuple[x509.Certificate, datetime]] = OrderedDict()

    async def verify(
        self,
        raw: bytes,
        headers: Mapping[str, str],
        webhook_id: str,
        now: datetime | None = None,
    ) -> None:
        """Raise WebhookRejected unless `raw` was signed by PayPal for this exact webhook ID."""
        try:
            await self._verify(raw, headers, webhook_id, now or datetime.now(UTC))
        except WebhookRejected:
            raise
        except VERIFY_ERRORS as exc:
            raise WebhookRejected("verification failed") from exc

    async def _verify(
        self, raw: bytes, headers: Mapping[str, str], webhook_id: str, now: datetime
    ) -> None:
        if not webhook_id:
            raise WebhookRejected("webhook id not configured")
        if len(raw) > MAX_BODY_BYTES:
            raise WebhookRejected("body too large")
        transmission_id = headers["paypal-transmission-id"]
        timestamp = headers["paypal-transmission-time"]
        if not SAFE_ID.fullmatch(transmission_id) or len(timestamp) > 64:
            raise WebhookRejected("transmission headers rejected")
        sent = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if sent.tzinfo is None or abs((now - sent).total_seconds()) > MAX_AGE_S:
            raise WebhookRejected("transmission outside time window")
        if headers["paypal-auth-algo"] != SIGNATURE_ALGO:
            raise WebhookRejected("signature algorithm rejected")
        encoded = headers["paypal-transmission-sig"]
        if len(encoded) > 2048:
            raise WebhookRejected("signature too large")
        signature = base64.b64decode(encoded, validate=True)
        cert = await self._certificate(headers["paypal-cert-url"], now)
        key = cert.public_key()
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise WebhookRejected("certificate key rejected")
        message = f"{transmission_id}|{timestamp}|{webhook_id}|{zlib.crc32(raw) & 0xFFFFFFFF}"
        key.verify(signature, message.encode(), padding.PKCS1v15(), hashes.SHA256())

    async def _certificate(self, url: str, now: datetime) -> x509.Certificate:
        validate_cert_url(url, self._hosts)
        cached = self._cache.get(url)
        if cached and now < cached[1]:
            cert = cached[0]
        else:
            cert = x509.load_pem_x509_certificate(await self._download(url))
            if self._issuer is None:
                self._issuer = x509.load_der_x509_certificate(
                    await self._download(self._issuer_url)
                )
            host = "messageverificationcerts." + (
                "sandbox.paypal.com" if "sandbox" in urlsplit(url).hostname else "paypal.com"
            )
            builder = PolicyBuilder().store(self._trust).time(now)
            chain = builder.build_server_verifier(x509.DNSName(host)).verify(cert, [self._issuer])
            deadline = min(now + CERT_CACHE_TTL, *(c.not_valid_after_utc for c in chain))
            self._cache[url] = cert, deadline
            self._cache.move_to_end(url)
            while len(self._cache) > CERT_CACHE_SIZE:
                self._cache.popitem(last=False)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise WebhookRejected("certificate outside validity period")
        return cert

    async def _download(self, url: str) -> bytes:
        raw = bytearray()
        async with self._client.stream("GET", url, follow_redirects=False, timeout=10) as r:
            if r.status_code != 200:
                raise WebhookRejected("certificate download rejected")
            async for chunk in r.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_CERT_BYTES:
                    raise WebhookRejected("certificate too large")
        return bytes(raw)


def parse_event(raw: bytes, app: str) -> WebhookEvent:
    """Identity of a (verified) event. `app` is TREASURY or VENDOR, taken from the route (R10)."""
    try:
        event = json.loads(raw)
        resource = event["resource"]
        if not isinstance(event, dict) or not isinstance(resource, dict):
            raise WebhookRejected("event rejected")
        event_id, event_type = event.get("id"), event.get("event_type")
        resource_id = _resource_id(resource)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise WebhookRejected("event rejected") from exc
    ids = (event_id, event_type, resource_id)
    if not all(isinstance(v, str) and SAFE_ID.fullmatch(v) for v in ids):
        raise WebhookRejected("event identity rejected")
    if not event_type.startswith(PREFIXES[app]):
        raise WebhookRejected("event type does not belong to this application")
    return WebhookEvent(event_id, event_type, resource_id, resource)


def _resource_id(resource: dict) -> object:
    """SPIKE-8: payout items use payout_item_id, batches payout_batch_id (maybe under
    batch_header); real invoicing events wrap the invoice, simulator events use a plain id."""
    header = resource.get("batch_header")
    invoice = resource.get("invoice")
    return (
        resource.get("payout_item_id")
        or resource.get("payout_batch_id")
        or (header.get("payout_batch_id") if isinstance(header, dict) else None)
        or (invoice.get("id") if isinstance(invoice, dict) else None)
        or resource.get("id")
    )
