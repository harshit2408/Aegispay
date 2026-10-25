"""SPIKE-8 only: receive signed sandbox evidence, never update application or money state.

Validate both HTTPS transport and the signing certificate's public CA chain and validity.
This probe is not the P1-09 production webhook receiver.
"""

import argparse
import base64
import binascii
import json
import os
import re
import ssl
import zlib
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
from fastapi import FastAPI, HTTPException, Request, Response

MAX_BYTES = 65536
CERT_HOSTS = {
    "api.paypal.com",
    "api-m.paypal.com",
    "api.sandbox.paypal.com",
    "api-m.sandbox.paypal.com",
}
SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
# Measured SPIKE-8 issuer. A future CA rotation fails closed until this probe is updated.
ISSUER_URL = "https://cacerts.digicert.com/DigiCertEVRSACAG2.crt"


def validate_cert_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in CERT_HOSTS
            and parsed.netloc == parsed.hostname
            and not parsed.query
            and not parsed.fragment
            and re.fullmatch(r"/v1/notifications/certs/CERT-[A-Za-z0-9-]+", parsed.path)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("certificate URL rejected")
    return url


class WebhookVerifier:
    def __init__(self, client: httpx.AsyncClient, trust_roots=None):
        self.client = client
        roots = (
            trust_roots
            if trust_roots is not None
            else [
                x509.load_der_x509_certificate(raw)
                for raw in ssl.create_default_context().get_ca_certs(binary_form=True)
            ]
        )
        self.trust = Store(roots)
        self.issuer = None
        self.cache: OrderedDict[str, tuple[x509.Certificate, datetime]] = OrderedDict()

    async def download(self, url: str) -> bytes:
        if url != ISSUER_URL:
            validate_cert_url(url)
        raw = bytearray()
        async with self.client.stream("GET", url, follow_redirects=False, timeout=10) as response:
            if response.status_code != 200:
                raise ValueError("certificate download rejected")
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise ValueError("certificate too large")
        return bytes(raw)

    async def certificate(self, url: str, now: datetime) -> x509.Certificate:
        validate_cert_url(url)
        cached = self.cache.get(url)
        if cached and now < cached[1] and now < cached[0].not_valid_after_utc:
            cert = cached[0]
        else:
            cert = x509.load_pem_x509_certificate(await self.download(url))
            if self.issuer is None:
                self.issuer = x509.load_der_x509_certificate(await self.download(ISSUER_URL))
            hostname = "messageverificationcerts."
            hostname += (
                "sandbox.paypal.com" if "sandbox" in urlsplit(url).hostname else "paypal.com"
            )
            policy = PolicyBuilder().store(self.trust).time(now)
            chain = policy.build_server_verifier(x509.DNSName(hostname)).verify(cert, [self.issuer])
            deadline = min(now + timedelta(hours=1), *(item.not_valid_after_utc for item in chain))
            self.cache[url] = cert, deadline
            self.cache.move_to_end(url)
            while len(self.cache) > 16:
                self.cache.popitem(last=False)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError("certificate outside validity period")
        return cert

    async def verify(self, raw: bytes, headers, webhook_id: str) -> dict:
        now = datetime.now(UTC)
        if len(raw) > MAX_BYTES:
            raise ValueError("body too large")
        transmission_id = headers["paypal-transmission-id"]
        timestamp = headers["paypal-transmission-time"]
        if not SAFE_ID.fullmatch(transmission_id) or len(timestamp) > 64:
            raise ValueError("transmission headers rejected")
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if when.tzinfo is None or abs((now - when).total_seconds()) > 600:
            raise ValueError("transmission outside time window")
        if headers["paypal-auth-algo"] != "SHA256withRSA":
            raise ValueError("signature algorithm rejected")
        encoded = headers["paypal-transmission-sig"]
        if len(encoded) > 2048:
            raise ValueError("signature too large")
        signature = base64.b64decode(encoded, validate=True)
        cert = await self.certificate(headers["paypal-cert-url"], now)
        public_key = cert.public_key()
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 2048:
            raise ValueError("certificate key rejected")
        message = f"{transmission_id}|{timestamp}|{webhook_id}|{zlib.crc32(raw) & 0xFFFFFFFF}"
        public_key.verify(signature, message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return {
            "received_at": now.isoformat(),
            "transmission_id": transmission_id,
            "delivery_age_ms": int((now - when).total_seconds() * 1000),
            "cert_sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "cert_url": headers["paypal-cert-url"],
            "certificate_chain_validated": True,
        }


def create_app(
    webhook_ids: dict[str, str],
    evidence_path: Path,
    *,
    client: httpx.AsyncClient | None = None,
    trust_roots=None,
) -> FastAPI:
    if set(webhook_ids) != {"treasury", "vendor"} or not all(webhook_ids.values()):
        raise ValueError("both registered webhook IDs are required")
    evidence_path = Path(evidence_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    owned_client = client is None
    cert_client = client or httpx.AsyncClient(verify=True, follow_redirects=False)
    verifier = WebhookVerifier(cert_client, trust_roots)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned_client:
            await cert_client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "spike": "SPIKE-8"}

    async def receive(request: Request, account: str, simulated: bool):
        if account not in webhook_ids:
            raise HTTPException(404)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise HTTPException(413, "body too large")
        try:
            metadata = await verifier.verify(
                bytes(raw), request.headers, "WEBHOOK_ID" if simulated else webhook_ids[account]
            )
            event = json.loads(raw)
            prefix = "PAYMENT.PAYOUT" if account == "treasury" else "INVOICING.INVOICE."
            if not isinstance(event, dict) or not isinstance(event.get("resource"), dict):
                raise ValueError("event rejected")
            event_id, event_type = event.get("id", ""), event.get("event_type", "")
            resource = event["resource"]
            resource_id = (
                resource.get("id")
                or resource.get("payout_item_id")
                or resource.get("payout_batch_id")
                or resource.get("batch_header", {}).get("payout_batch_id")
                or resource.get("invoice", {}).get("id")
            )
            if not all(
                isinstance(value, str) and SAFE_ID.fullmatch(value)
                for value in (event_id, event_type, resource_id)
            ) or not event_type.startswith(prefix):
                raise ValueError("event identity or application rejected")
        except (
            KeyError,
            ValueError,
            TypeError,
            AttributeError,
            InvalidSignature,
            VerificationError,
            binascii.Error,
            httpx.HTTPError,
        ) as exc:
            raise HTTPException(400, "webhook verification failed") from exc
        metadata.update(
            app=account,
            simulated=simulated,
            verified=True,
            event_id=event_id,
            event_type=event_type,
            resource_id=resource_id,
        )
        # Receipt evidence only. No payloads, tokens, emails, or settlement side effects.
        with evidence_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(metadata) + "\n")
        return Response(status_code=204)

    @app.post("/api/webhooks/paypal/{account}")
    async def real(request: Request, account: str):
        return await receive(request, account, False)

    @app.post("/simulator/{account}")
    async def simulator(request: Request, account: str):
        return await receive(request, account, True)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    ids = {app: os.environ.get(f"{app.upper()}_WEBHOOK_ID", "") for app in ("treasury", "vendor")}
    uvicorn.run(create_app(ids, args.evidence), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
