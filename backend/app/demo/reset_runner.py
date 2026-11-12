"""Owner-credentialed live demo reset (PRD §13 POST /api/demo/reset).

The API only records a DEMO_RESET_REQUEST: its database role cannot create POs, GRNs, mandates or
vendor identity (R15, R16, R22). This runner holds the owner connection and the Vendor credential,
reuses the P1-10 seeder to create a fresh run suffix, and records the outcome. Run exactly one
instance per deployment: python -m app.demo --config <seed.json> --apply-db --send-invoices
--serve-resets. Earlier runs are not deleted; they drop out of the grid (derived archive).
"""

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.audit.events import enqueue_event
from app.config import Settings
from app.demo.runtime import RESET_DONE, RESET_FAILED, pending_reset
from app.demo.seeder import DemoSeed, seed_demo
from app.models.core import MandatePolicy

log = logging.getLogger("aegis.demo.reset")
ACTOR = "demo-reset-runner"
POLL_SECONDS = 2.0


async def process_pending(
    sessions,
    *,
    template: DemoSeed,
    settings: Settings,
    manifest_dir: Path,
    gateway=None,
) -> str | None:
    """Handle the newest pending request, if any. Returns its request id."""
    async with sessions() as session:
        pending = await pending_reset(session)
        active = await session.scalar(
            select(MandatePolicy.policy_id).where(MandatePolicy.is_active)
        )
        await session.rollback()
    if pending is None:
        return None
    request_id = pending[0]
    run_id = uuid4().hex[:12]
    previous = active.removeprefix("demo-") if active and active.startswith("demo-") else None
    try:
        await seed_demo(
            sessions,
            config=template.model_copy(update={"run_id": run_id}),
            settings=settings,
            manifest_dir=manifest_dir,
            gateway=gateway,
        )
        kind = RESET_DONE
        payload = {"request_id": request_id, "run_id": run_id, "previous_run_id": previous}
    except Exception as error:  # class name only: PayPal/DB messages stay out of the audit chain
        log.error("demo_reset_failed node=reset error=%s", type(error).__name__)
        kind = RESET_FAILED
        payload = {"request_id": request_id, "error": type(error).__name__}
    async with sessions() as session, session.begin():
        await enqueue_event(
            session, execution_id="demo-control", actor=ACTOR, event_type=kind, payload=payload
        )
    return request_id


async def serve(sessions, *, stop: asyncio.Event | None = None, **kwargs) -> None:
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            await process_pending(sessions, **kwargs)
        except Exception as error:
            log.error("demo_reset_poll_failed node=reset error=%s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except TimeoutError:
            pass
