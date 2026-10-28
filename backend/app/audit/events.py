"""PRD §8/R21: unsigned API queue and settlement-owned global HMAC event chain.

The caller owns the transaction and supplies an already-redacted payload and authenticated actor.
Only settlement supplies the signing key; importing this module reads no environment or credentials.
The settlement worker drains the queue and publishes external checkpoints (R21).
"""

import hashlib
import hmac
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payables import AuditQueue, ExecutionEvent

GENESIS_HASH = "0" * 64
_CHAIN_LOCK = 0x4145474953415544  # One global chain, including distinct executions.


class AuditIntegrityError(ValueError):
    """A signed row, its position or the trusted expected head does not match."""


def _key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("audit signing key must contain at least 32 bytes")


def _json(value: Any) -> str:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("audit JSON object keys must be strings")
        for item in value.values():
            _json(item)
    elif isinstance(value, list):
        for item in value:
            _json(item)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("audit payload must contain only JSON values")
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("audit payload must contain only finite JSON values") from exc


def _timestamp(ts: datetime) -> str:
    if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("audit timestamp must be timezone-aware")
    return ts.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _digest(event: ExecutionEvent, key: bytes) -> str:
    row = {
        "event_seq": event.event_seq,
        "execution_id": event.execution_id,
        "ts": _timestamp(event.ts),
        "actor": event.actor,
        "event_type": event.event_type,
        "payload": event.payload,
        "prev_hash": event.prev_hash,
    }
    message = event.prev_hash.encode("ascii") + _json(row).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_chain(events: Iterable[ExecutionEvent], *, key: bytes, expected_head: str) -> str:
    """Verify a complete ordered chain against a caller-trusted head (not a DB-derived anchor)."""
    _key(key)
    previous, sequence = GENESIS_HASH, 0
    for event in events:
        try:
            valid = (
                type(event.event_seq) is int
                and event.event_seq > sequence
                and event.prev_hash == previous
                and hmac.compare_digest(event.hash, _digest(event, key))
            )
        except (TypeError, ValueError, UnicodeError, AttributeError) as exc:
            raise AuditIntegrityError("audit event is malformed or unauthenticated") from exc
        if not valid:
            raise AuditIntegrityError("audit event or chain order does not match")
        previous, sequence = event.hash, event.event_seq
    if (
        not isinstance(expected_head, str)
        or not expected_head.isascii()
        or not hmac.compare_digest(previous, expected_head)
    ):
        raise AuditIntegrityError("audit chain does not match the trusted head")
    return previous


def _validate(execution_id: str, actor: str, event_type: str, payload: dict, ts: datetime) -> None:
    if any(not isinstance(value, str) or not value for value in (execution_id, actor, event_type)):
        raise ValueError("audit execution, actor and event type must be nonempty strings")
    if not isinstance(payload, dict):
        raise ValueError("audit payload must be a JSON object")
    _json(payload)
    _timestamp(ts)


async def enqueue_event(
    session: AsyncSession,
    *,
    execution_id: str,
    actor: str,
    event_type: str,
    payload: dict,
    ts: datetime | None = None,
) -> AuditQueue:
    """Append an unsigned queue row in the caller's transaction; needs no signing key."""
    ts = ts if ts is not None else datetime.now(UTC)
    _validate(execution_id, actor, event_type, payload, ts)
    if not session.in_transaction():
        raise ValueError("audit enqueue requires a caller-owned transaction")
    queued = AuditQueue(
        execution_id=execution_id,
        actor=actor,
        event_type=event_type,
        payload=json.loads(_json(payload)),
        ts=ts,
    )
    session.add(queued)
    await session.flush()
    return queued


async def lock_chain(session: AsyncSession) -> None:
    """Serialize signed-chain writers; unsigned producers never acquire this lock."""
    if not session.in_transaction():
        raise ValueError("audit write requires a caller-owned transaction")
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("audit write requires Postgres advisory locking")
    if await session.scalar(text("SHOW transaction_isolation")) != "read committed":
        raise ValueError("audit write requires READ COMMITTED isolation")
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": _CHAIN_LOCK})


async def append_event(
    session: AsyncSession,
    *,
    key: bytes,
    execution_id: str,
    actor: str,
    event_type: str,
    payload: dict,
    ts: datetime | None = None,
) -> ExecutionEvent:
    """Settlement-only append; caller commits/rolls back while holding the chain lock."""
    _key(key)
    ts = ts if ts is not None else datetime.now(UTC)
    _validate(execution_id, actor, event_type, payload, ts)
    await lock_chain(session)
    rows = list(
        await session.scalars(
            select(ExecutionEvent)
            .order_by(ExecutionEvent.event_seq)
            .execution_options(populate_existing=True)
        )
    )
    head = rows[-1].hash if rows else GENESIS_HASH
    # shortcut: O(n) prefix verification; upgrade to verified checkpoints for large chains.
    verify_chain(rows, key=key, expected_head=head)
    sequence = await session.scalar(
        text("SELECT nextval(pg_get_serial_sequence('execution_events', 'event_seq'))")
    )
    # JSONB can normalize numeric notation; sign precisely what a later DB read will return.
    stored_payload = await session.scalar(
        text("SELECT CAST(:payload AS JSONB) AS payload"), {"payload": _json(payload)}
    )
    event = ExecutionEvent(
        event_seq=sequence,
        execution_id=execution_id,
        actor=actor,
        event_type=event_type,
        payload=stored_payload,
        ts=ts,
        prev_hash=head,
    )
    event.hash = _digest(event, key)
    session.add(event)
    await session.flush()
    return event
