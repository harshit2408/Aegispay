"""P2-13: settlement-side health probes for GET /api/health (PRD §13, R12).

Settlement holds both PayPal clients and both account emails, so it probes and reports them; the
API holds neither Treasury credential nor email (R22) and only reads the stored booleans. Nothing
here can raise into the money loops, and no secret, identifier or email is stored or logged.
"""

import asyncio
import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.models.payables import ServiceHealth

log = logging.getLogger("aegis.health")
INTERVAL_SECONDS = 30


async def _token_ok(client) -> bool:
    try:
        await client.verify_token()
    except Exception:
        return False
    return True


async def _webhook_ok(client, webhook_id: str) -> bool:
    if not webhook_id.strip():
        return False
    try:
        response = await client.get("/v1/notifications/webhooks")
        registered = response.body.get("webhooks", [])
        return any(isinstance(row, dict) and row.get("id") == webhook_id for row in registered)
    except Exception:
        return False


async def probe(treasury, vendor, settings) -> dict[str, bool]:
    """Keys are exactly HEALTH_CHECKS."""
    treasury_email = settings.treasury_account_email.strip().casefold()
    vendor_email = settings.vendor_account_email.strip().casefold()
    return {
        "treasury_token": await _token_ok(treasury),
        "vendor_token": await _token_ok(vendor),
        "treasury_webhook": await _webhook_ok(treasury, settings.treasury_webhook_id),
        "vendor_webhook": await _webhook_ok(vendor, settings.vendor_webhook_id),
        "accounts_distinct": bool(treasury_email) and treasury_email != vendor_email,
    }


async def record(sessions, details: dict[str, bool]) -> None:
    now = func.now()
    statement = insert(ServiceHealth).values(service="settlement", checked_at=now, details=details)
    statement = statement.on_conflict_do_update(
        index_elements=[ServiceHealth.service], set_={"checked_at": now, "details": details}
    )
    async with sessions() as session, session.begin():
        await session.execute(statement)


async def run_forever(sessions, treasury, vendor, settings, stop: asyncio.Event) -> None:
    """Health is advisory: any failure is logged by class and the loop keeps running."""
    while not stop.is_set():
        try:
            await record(sessions, await probe(treasury, vendor, settings))
        except Exception as error:
            log.warning("health_probe_failed node=health error=%s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
        except TimeoutError:
            pass
