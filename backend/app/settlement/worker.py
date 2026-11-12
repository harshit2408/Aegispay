"""Independent settlement process: python -m app.settlement.worker (R19, R22)."""

import asyncio
import logging
import shutil
import signal
import time

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audit import consumer as audit_consumer
from app.audit.anchor import AnchorConflict, AnchorUnavailable, GitAnchors
from app.audit.consumer import AuditConsumer
from app.config import get_settlement_settings
from app.paypal.client import PayPalClient
from app.paypal.payouts import Payouts
from app.service_security import validate_database_role, validate_service_environment
from app.settlement import controls, health, outbox_worker, reconciler
from app.settlement.reconciler import Reconciler
from app.settlement.service import SettlementService
from app.settlement.webhook_consumer import WebhookConsumer
from app.vendor_sim.gateway import Invoicing, VendorGateway


async def _consume(consumer: WebhookConsumer, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await consumer.run_once()
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            pass


STARTUP_DRAIN_ATTEMPTS = 5
_transient = audit_consumer.is_transient


async def _startup_drain(audit: AuditConsumer, stop: asyncio.Event) -> None:
    """Startup may race producer locks: retry a bounded number of times, then fail."""
    attempt, outage_since = 0, None
    while (attempt := attempt + 1) <= STARTUP_DRAIN_ATTEMPTS:
        try:
            await audit_consumer.run_once_until_stopped(audit, stop)
            return
        except Exception as exc:
            if audit_consumer.is_anchor_outage(exc):
                # Same bounded grace as runtime (docs/AUDIT.md); not counted as an attempt.
                now = time.monotonic()
                outage_since = now if outage_since is None else outage_since
                if now - outage_since >= audit_consumer.TRANSIENT_WINDOW_SECONDS:
                    raise
                attempt -= 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                    return
                except TimeoutError:
                    continue
            if not _transient(exc) or attempt == STARTUP_DRAIN_ATTEMPTS:
                raise
            logging.getLogger("aegis.audit").warning(
                "audit_startup_drain_retry node=audit error=%s attempt=%d",
                type(exc).__name__,
                attempt,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=attempt)
                return
            except TimeoutError:
                pass


def _require_git(anchor_repo: str | None) -> None:
    """The anchor needs `git`; INSTALL_GIT=0 images lack it. Fixed message, fail closed (R21)."""
    if anchor_repo and shutil.which("git") is None:
        logging.getLogger("aegis.audit").error(
            "audit_anchor_git_missing node=audit action=stop_worker hint=build_with_INSTALL_GIT=1"
        )
        raise RuntimeError(
            "Audit anchor requires git; build the settlement image with INSTALL_GIT=1"
        )


async def _controls(sessions, settings, stop):
    while not stop.is_set():
        await controls.run_once(sessions, settings)
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            pass


async def run(stop: asyncio.Event | None = None) -> None:
    validate_service_environment("settlement")
    settings = get_settlement_settings()
    _require_git(settings.audit_anchor_repo)
    engine = create_async_engine(settings.settlement_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    treasury = vendor = None
    old_signals = {}
    loop = asyncio.get_running_loop()
    if stop is None:
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_signals[sig] = signal.getsignal(sig)
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows' event loop has no add_signal_handler
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        try:
            async with sessions() as session:
                await validate_database_role(session, "settlement")
        except Exception:
            raise RuntimeError("Settlement database startup validation failed") from None
        audit = AuditConsumer(
            sessions,
            key=settings.audit_hmac_key.get_secret_value().encode(),
            anchors=GitAnchors(
                settings.audit_anchor_repo,
                settings.audit_anchor_token.get_secret_value()
                if settings.audit_anchor_token
                else None,
                token_hosts=settings.anchor_token_hosts,
            )
            if settings.audit_anchor_repo
            else None,
        )
        # Verify anchored history before enabling any money movement.
        try:
            await _startup_drain(audit, stop)
        except AnchorConflict:
            # Repeated publication races may delay startup, but are not an integrity incident.
            raise
        except (AnchorUnavailable, audit_consumer.StaleAnchor):
            logging.getLogger("aegis.audit").error(
                "audit_anchor_unavailable node=audit action=stop_worker"
            )
            await audit.persist_halt("AUDIT_ANCHOR_UNAVAILABLE")  # best-effort, never raises
            raise
        if stop.is_set():
            return
        treasury = PayPalClient(
            app="TREASURY",
            base_url=settings.paypal_base_url,
            client_id=settings.treasury_client_id,
            client_secret=settings.treasury_client_secret.get_secret_value(),
        )
        vendor = PayPalClient(
            app="VENDOR",
            base_url=settings.paypal_base_url,
            client_id=settings.vendor_client_id,
            client_secret=settings.vendor_client_secret.get_secret_value(),
        )
        payouts = Payouts(treasury)
        gateway = VendorGateway(Invoicing(vendor))
        await controls.run_once(sessions, settings)
        service = SettlementService(sessions, payouts, gateway)
        service.velocity_limits = (
            settings.velocity_max_payouts_per_hour,
            settings.velocity_max_cents_per_hour,
        )
        poller = Reconciler(sessions, payouts, gateway)
        consumer = WebhookConsumer(sessions, poller)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(outbox_worker.run_forever(sessions, service, stop))
            tasks.create_task(reconciler.run_forever(poller, sessions, stop))
            tasks.create_task(_consume(consumer, stop))
            tasks.create_task(_controls(sessions, settings, stop))
            tasks.create_task(health.run_forever(sessions, treasury, vendor, settings, stop))
            tasks.create_task(audit_consumer.run_forever(audit, stop))
    finally:
        stop.set()
        for sig, old_handler in old_signals.items():
            try:
                loop.remove_signal_handler(sig)
            except NotImplementedError:
                pass
            signal.signal(sig, old_handler)
        try:
            if treasury is not None:
                await treasury.aclose()
        finally:
            try:
                if vendor is not None:
                    await vendor.aclose()
            finally:
                await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
