"""Settlement-owned vendor changes and velocity breaker; no HTTP under database locks."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models.core import VendorMaster, VendorMasterChange
from app.models.payables import Payout
from app.ops.alerts import deliver_alerts
from app.settlement.halt import halt


async def apply_vendor_changes(session, now: datetime):
    rows = (
        await session.scalars(
            select(VendorMasterChange)
            .where(VendorMasterChange.status == "COOLING")
            .order_by(VendorMasterChange.change_id)
            .with_for_update()
        )
    ).all()
    for row in rows:
        vendor = await session.get(VendorMaster, row.vendor_id, with_for_update=True)
        if vendor is None or vendor.trust_status == "BLOCKED":
            continue
        if row.effective_at > now:
            vendor.trust_status = "COOLING"
            continue
        if any(getattr(vendor, key, None) != value for key, value in row.old_values.items()):
            await halt(session, "VENDOR_CHANGE_CONFLICT")
            continue
        for key in ("paypal_payer_id", "verified_paypal_email"):
            setattr(vendor, key, row.new_values[key])
        vendor.version += 1
        vendor.trust_status, vendor.payee_last_changed_at = "VERIFIED", now
        row.status = "EFFECTIVE"


async def check_velocity(session, max_count: int, max_cents: int, now: datetime):
    since = now - timedelta(hours=1)
    count, cents = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Payout.amount_cents), 0)).where(
                Payout.created_at >= since
            )
        )
    ).one()
    failed = await session.scalar(
        select(func.count())
        .select_from(Payout)
        .where(
            Payout.item_status.in_(("FAILED", "RETURNED")),
            func.coalesce(Payout.last_polled_at, Payout.created_at) >= since,
        )
    )
    if count > max_count or cents > max_cents or failed >= 3:
        await halt(session, "VELOCITY_BREAKER")
        return True
    return False


async def run_once(sessions, settings):
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        await apply_vendor_changes(session, now)
        await check_velocity(
            session,
            settings.velocity_max_payouts_per_hour,
            settings.velocity_max_cents_per_hour,
            now,
        )
    await deliver_alerts(sessions, settings.alert_webhook_url)
