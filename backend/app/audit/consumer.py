"""Settlement-owned ordered audit drain, crash-safe checkpoint publication and verification."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text

from app.audit.anchor import AnchorUnavailable, GitAnchors, Snapshot
from app.audit.events import (
    GENESIS_HASH,
    AuditIntegrityError,
    _key,
    append_event,
    lock_chain,
    verify_chain,
)
from app.models.payables import AuditAnchor, AuditQueue, ExecutionEvent
from app.settlement.halt import halt

log = logging.getLogger("aegis.audit")


async def verified_rows(session, *, key: bytes, snapshot: Snapshot) -> list[ExecutionEvent]:
    """DB receipts can reject remote rollback, but never supply the trusted checkpoint."""
    rows = list(
        await session.scalars(
            select(ExecutionEvent)
            .order_by(ExecutionEvent.event_seq)
            .execution_options(populate_existing=True)
        )
    )
    receipt = await session.scalar(
        select(AuditAnchor).order_by(AuditAnchor.anchor_id.desc()).limit(1)
    )
    checkpoint = snapshot.checkpoint
    if receipt and (
        checkpoint is None
        or receipt.external_ref not in snapshot.commits
        or receipt.event_seq > checkpoint.event_seq
    ):
        raise AuditIntegrityError("external audit history regressed or disappeared")
    if checkpoint:
        prefix = [row for row in rows if row.event_seq <= checkpoint.event_seq]
        if (prefix[-1].event_seq if prefix else 0) != checkpoint.event_seq:
            raise AuditIntegrityError("database lost its externally anchored prefix")
        verify_chain(prefix, key=key, expected_head=checkpoint.head_hash)
    # The suffix is HMAC-authenticated, but only the external prefix is anchored.
    verify_chain(rows, key=key, expected_head=rows[-1].hash if rows else GENESIS_HASH)
    return rows


async def _receipt(session, snapshot: Snapshot) -> None:
    checkpoint = snapshot.checkpoint
    if checkpoint and not await session.scalar(
        select(AuditAnchor.anchor_id)
        .where(AuditAnchor.external_ref == checkpoint.external_ref)
        .limit(1)
    ):
        session.add(
            AuditAnchor(
                event_seq=checkpoint.event_seq,
                head_hash=checkpoint.head_hash,
                external_ref=checkpoint.external_ref,
                created_at=checkpoint.created_at,
            )
        )
        await session.flush()


class AuditConsumer:
    def __init__(self, sessions, *, key: bytes, anchors: GitAnchors | None):
        _key(key)
        self.sessions, self.key, self.anchors = sessions, key, anchors

    async def run_once(self, *, now: datetime | None = None) -> int:
        try:
            try:
                return await self._run_once(now=now)
            except AuditIntegrityError:
                if self.anchors is None:
                    raise
                # Refetch outside the rolled-back transaction if a publisher advanced the receipt.
                return await self._run_once(now=now)
        except AuditIntegrityError:
            try:
                async with self.sessions() as session, session.begin():
                    await halt(session, "AUDIT_INTEGRITY_FAILED", changed_by="system:audit")
            except Exception:
                log.error("audit_integrity_halt_persist_failed node=audit action=stop_worker")
            raise

    async def _run_once(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        snapshot = await self.anchors.read() if self.anchors else Snapshot(None)
        async with self.sessions() as session, session.begin():
            await lock_chain(session)
            await verified_rows(session, key=self.key, snapshot=snapshot)
            # Wait for earlier INSERT transactions, without adding a lock to money producers.
            await session.execute(text("LOCK TABLE audit_queue IN SHARE ROW EXCLUSIVE MODE"))
            queued = list(
                await session.scalars(select(AuditQueue).order_by(AuditQueue.queue_seq).limit(100))
            )
            for row in queued:
                await append_event(
                    session,
                    key=self.key,
                    execution_id=row.execution_id,
                    actor=row.actor,
                    event_type=row.event_type,
                    payload=row.payload,
                    ts=row.ts,
                )
            if queued:
                await session.execute(
                    delete(AuditQueue).where(
                        AuditQueue.queue_seq.in_([row.queue_seq for row in queued])
                    )
                )
            await _receipt(session, snapshot)
        if self.anchors:
            await self._publish_if_due(snapshot, now)
        return len(queued)

    async def _publish_if_due(self, snapshot: Snapshot, now: datetime) -> None:
        async with self.sessions() as session, session.begin():
            await lock_chain(session)
            rows = await verified_rows(session, key=self.key, snapshot=snapshot)
            checkpoint = snapshot.checkpoint
            settled = any(
                row.event_type == "SETTLED"
                and row.event_seq > (checkpoint.event_seq if checkpoint else 0)
                for row in rows
            )
            if checkpoint and not settled and now - checkpoint.created_at < timedelta(hours=1):
                return
            sequence, head = (rows[-1].event_seq, rows[-1].hash) if rows else (0, GENESIS_HASH)
        # Never keep any database lock or transaction open across Git/network I/O.
        published = await self.anchors.publish(
            snapshot, event_seq=sequence, head_hash=head, now=now
        )
        async with self.sessions() as session, session.begin():
            await lock_chain(session)
            await verified_rows(session, key=self.key, snapshot=published)
            await _receipt(session, published)


async def run_forever(consumer: AuditConsumer, stop: asyncio.Event) -> None:
    if consumer.anchors is None:
        log.warning("audit_external_anchor_disabled mode=HMAC_ONLY exports=unavailable")
    while not stop.is_set():
        try:
            await consumer.run_once()
        except AuditIntegrityError:
            # Kill the worker before more settlement work can run on a compromised history.
            log.error("audit_integrity_failed node=audit action=stop_worker")
            raise
        except AnchorUnavailable:
            log.error("audit_anchor_unavailable node=audit action=retry")
        except Exception:
            # No exception text: drivers and credential helpers may include private values.
            log.error("audit_drain_failed node=audit action=retry")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            pass
