"""PayPal webhook routes (PRD §9.4, R10, R25).

Order: size cap -> verify from the raw bytes -> check the resource is ours -> dedupe by event id
-> store. A verified webhook is stored as a hint for the settlement process, which confirms it with
a GET before any money transition; this router writes no money state. Simulator events verify with
the literal WEBHOOK_ID, are stored `simulated=true` and are never consumed; their routes exist only
when DEMO_ENABLED (R24). Unverified bodies keep a 2 KB sample only (R25).
"""

import hashlib
import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.payables import InvoiceExecution, Payout, WebhookEvent
from app.paypal.webhooks import (
    LIVE_HOSTS,
    MAX_BODY_BYTES,
    SANDBOX_HOSTS,
    WebhookRejected,
    WebhookVerifier,
    parse_event,
)

log = logging.getLogger("aegis.webhooks")

SAMPLE_BYTES = 2048
KEPT_HEADERS = (
    "paypal-transmission-id",
    "paypal-transmission-time",
    "paypal-auth-algo",
    "paypal-cert-url",
)
SIMULATOR_WEBHOOK_ID = "WEBHOOK_ID"
APPS = {"treasury": "TREASURY", "vendor": "VENDOR"}

router = APIRouter(prefix="/api/webhooks/paypal")
simulator_router = APIRouter(prefix="/api/webhooks/paypal/simulator")


@lru_cache
def get_verifier() -> WebhookVerifier:
    sandbox = "sandbox" in get_settings().paypal_base_url
    client = httpx.AsyncClient(verify=True, follow_redirects=False)
    return WebhookVerifier(client, allowed_hosts=SANDBOX_HOSTS if sandbox else LIVE_HOSTS)


def get_webhook_ids() -> dict[str, str]:
    settings = get_settings()
    return {"TREASURY": settings.treasury_webhook_id, "VENDOR": settings.vendor_webhook_id}


Session = Annotated[AsyncSession, Depends(get_session)]
Verifier = Annotated[WebhookVerifier, Depends(get_verifier)]
WebhookIds = Annotated[dict[str, str], Depends(get_webhook_ids)]


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, "body too large")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, "body too large")
    return bytes(raw)


async def _is_ours(session: AsyncSession, app: str, resource_id: str) -> bool:
    if app == "TREASURY":
        query = select(
            exists().where(
                (Payout.payout_item_id == resource_id) | (Payout.payout_batch_id == resource_id)
            )
        )
    else:
        query = select(exists().where(InvoiceExecution.paypal_invoice_id == resource_id))
    return bool((await session.execute(query)).scalar())


async def _store(session: AsyncSession, **values: object) -> None:
    statement = pg_insert(WebhookEvent).values(**values)
    await session.execute(statement.on_conflict_do_nothing(index_elements=["event_id"]))
    await session.commit()


async def _store_unverified(session: AsyncSession, app: str, raw: bytes, request: Request) -> None:
    await _store(
        session,
        event_id="unverified-" + hashlib.sha256(raw).hexdigest()[:32],
        app=app,
        event_type="UNVERIFIED",
        resource_id="-",
        signature_verified=False,
        simulated=False,
        headers_json={k: request.headers.get(k, "")[:256] for k in KEPT_HEADERS},
        raw_body=raw[:SAMPLE_BYTES],
        processed_at=datetime.now(UTC),  # nothing to consume
    )


async def receive(
    request: Request,
    app: str,
    simulated: bool,
    session: AsyncSession,
    verifier: WebhookVerifier,
    webhook_ids: dict[str, str],
) -> Response:
    raw = await _read_capped(request)
    try:
        await verifier.verify(
            raw, request.headers, SIMULATOR_WEBHOOK_ID if simulated else webhook_ids[app]
        )
        event = parse_event(raw, app)
    except WebhookRejected as err:
        await _store_unverified(session, app, raw, request)
        log.warning("ALERT webhook rejected app=%s simulated=%s reason=%s", app, simulated, err)
        raise HTTPException(400, "webhook verification failed") from None
    if not simulated and not await _is_ours(session, app, event.resource_id):
        log.info("webhook for a resource we did not create ignored app=%s", app)
        return Response(status_code=204)  # acknowledged so PayPal stops retrying
    await _store(
        session,
        event_id=event.event_id,
        app=app,
        event_type=event.event_type,
        resource_id=event.resource_id,
        signature_verified=True,
        simulated=simulated,
        headers_json={k: request.headers.get(k, "")[:256] for k in KEPT_HEADERS},
        raw_body=raw,
        processed_at=datetime.now(UTC) if simulated else None,  # simulated never reaches money
    )
    return Response(status_code=204)


@router.post("/treasury")
async def treasury(request: Request, session: Session, verifier: Verifier, ids: WebhookIds):
    return await receive(request, "TREASURY", False, session, verifier, ids)


@router.post("/vendor")
async def vendor(request: Request, session: Session, verifier: Verifier, ids: WebhookIds):
    return await receive(request, "VENDOR", False, session, verifier, ids)


@simulator_router.post("/{account}")
async def simulator(
    account: str, request: Request, session: Session, verifier: Verifier, ids: WebhookIds
):
    if account not in APPS or not get_settings().demo_enabled:  # R24
        raise HTTPException(404)
    return await receive(request, APPS[account], True, session, verifier, ids)
