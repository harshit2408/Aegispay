"""Read-only demo-control state used outside the demo package (R24).

Kept apart from `app.demo` so production routers never load demo code. Newest-wins reads order by
the database sequence, never by the caller-supplied `ts`: every queued row is newer than every
signed row, because settlement drains the queue into the chain in queue order.
"""

from sqlalchemy import select

from app.models.core import MandatePolicy
from app.models.payables import AuditQueue, ExecutionEvent

CONTROL_ID = "demo-control"
LIVE_DETECTOR = "DEMO_LIVE_DETECTOR"  # live-mode toggle, bound to one seeded run


async def latest_for_run(session, event_type: str, run_id: str) -> dict | None:
    """Newest demo-control payload of this type recorded for this seeded run."""
    for table, sequence in (
        (AuditQueue, AuditQueue.queue_seq),
        (ExecutionEvent, ExecutionEvent.event_seq),
    ):
        payload = await session.scalar(
            select(table.payload)
            .where(
                table.execution_id == CONTROL_ID,
                table.event_type == event_type,
                table.payload["run_id"].astext == run_id,
            )
            .order_by(sequence.desc())
            .limit(1)
        )
        if payload is not None:
            return payload
    return None


async def live_detector(session) -> bool:
    """Detector for the active demo run: ON unless an admin turned it off for this run.

    A non-demo mandate always gets the detector; a reset seeds a new run, so it is ON again.
    """
    active = await session.scalar(select(MandatePolicy.policy_id).where(MandatePolicy.is_active))
    if not active or not active.startswith("demo-"):
        return True
    payload = await latest_for_run(session, LIVE_DETECTOR, active.removeprefix("demo-"))
    return not (payload and payload.get("detector_enabled") is False)  # fail safe: ON
