"""P1-11: global HMAC chain, transaction safety and trusted-head verification (R21)."""

import asyncio
import copy
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select, text

from app.audit.events import AuditIntegrityError, append_event, enqueue_event, verify_chain
from app.models.payables import AuditQueue, ExecutionEvent

KEY = b"test-audit-key-not-a-real-secret!!"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
GENESIS = "0" * 64


async def _append(sm, number=0):
    async with sm() as session, session.begin():
        return await append_event(
            session,
            key=KEY,
            execution_id=f"execution-{number}",
            actor="settlement",
            event_type="PAYOUT_SUBMITTED",
            payload={"amount_cents": 1234, "number": number},
            ts=NOW,
        )


async def _events(sm):
    async with sm() as session:
        return list(
            await session.scalars(select(ExecutionEvent).order_by(ExecutionEvent.event_seq))
        )


def test_empty_chain_requires_the_expected_genesis_head():
    assert verify_chain([], key=KEY, expected_head=GENESIS) == GENESIS
    with pytest.raises(AuditIntegrityError):
        verify_chain([], key=KEY, expected_head="f" * 64)


@pytest.mark.parametrize("head", [None, "", "é" * 64])
def test_verification_rejects_malformed_trusted_heads(head):
    with pytest.raises(AuditIntegrityError):
        verify_chain([], key=KEY, expected_head=head)


@pytest.mark.parametrize("key", [b"", b"short", "not bytes", None])
def test_verification_rejects_invalid_signing_keys(key):
    with pytest.raises(ValueError):
        verify_chain([], key=key, expected_head=GENESIS)


async def test_append_roundtrips_all_signed_fields_and_jsonb_numbers(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        event = await append_event(
            session,
            key=KEY,
            execution_id="invoice-1",
            actor="controller-1",
            event_type="ANALYZED",
            payload={"amount_cents": 1234, "confidence": 0.5, "large": 1e20, "unicode": "é"},
            ts=NOW,
        )
        head = event.hash
    rows = await _events(pg_sessionmaker)
    assert rows[0].prev_hash == GENESIS
    assert rows[0].payload["amount_cents"] == 1234
    assert verify_chain(rows, key=KEY, expected_head=head) == head


async def test_concurrent_appends_across_executions_form_one_chain(pg_sessionmaker):
    await asyncio.gather(*(_append(pg_sessionmaker, number) for number in range(12)))
    rows = await _events(pg_sessionmaker)
    assert len(rows) == 12
    assert len({row.execution_id for row in rows}) == 12
    assert verify_chain(rows, key=KEY, expected_head=rows[-1].hash) == rows[-1].hash
    assert all(
        row.prev_hash == previous.hash for previous, row in zip(rows, rows[1:], strict=False)
    )


async def test_rollback_leaves_chain_intact_and_sequence_gaps_are_valid(pg_sessionmaker):
    first = await _append(pg_sessionmaker)
    with pytest.raises(RuntimeError, match="rollback"):
        async with pg_sessionmaker() as session, session.begin():
            await append_event(
                session,
                key=KEY,
                execution_id="discard",
                actor="settlement",
                event_type="TEST",
                payload={},
                ts=NOW,
            )
            raise RuntimeError("rollback")
    last = await _append(pg_sessionmaker, 2)
    rows = await _events(pg_sessionmaker)
    assert len(rows) == 2 and last.event_seq > first.event_seq + 1
    verify_chain(rows, key=KEY, expected_head=last.hash)


@pytest.mark.parametrize(
    "field",
    ["execution_id", "actor", "event_type", "payload", "ts", "event_seq", "prev_hash", "hash"],
)
async def test_every_persisted_field_is_authenticated(pg_sessionmaker, field):
    event = await _append(pg_sessionmaker)
    head = event.hash
    replacements = {
        "execution_id": "different",
        "actor": "admin",
        "event_type": "SETTLED",
        "payload": {"amount_cents": 1235},
        "ts": datetime(2026, 10, 27, tzinfo=UTC),
        "event_seq": event.event_seq + 1,
        "prev_hash": "a" * 64,
        "hash": "b" * 64,
    }
    setattr(event, field, replacements[field])
    with pytest.raises(AuditIntegrityError):
        verify_chain([event], key=KEY, expected_head=head)


async def test_missing_reordered_rows_wrong_key_and_truncated_suffix_fail(pg_sessionmaker):
    for number in range(3):
        await _append(pg_sessionmaker, number)
    rows = await _events(pg_sessionmaker)
    head = rows[-1].hash
    for changed in (rows[1:], rows[:-1], [rows[0], rows[2]], list(reversed(rows))):
        with pytest.raises(AuditIntegrityError):
            verify_chain(changed, key=KEY, expected_head=head)
    with pytest.raises(AuditIntegrityError):
        verify_chain(rows, key=b"x" * 32, expected_head=head)


async def test_queue_is_unsigned_and_does_not_write_the_chain(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        queued = await enqueue_event(
            session,
            execution_id="invoice-1",
            actor="controller-1",
            event_type="UPLOADED",
            payload={"document_id": "document-1"},
            ts=NOW,
        )
        assert queued.queue_seq > 0
    assert await _events(pg_sessionmaker) == []
    async with pg_sessionmaker() as session:
        assert (await session.scalars(select(AuditQueue))).one().event_type == "UPLOADED"


async def test_append_requires_a_caller_transaction(pg_sessionmaker):
    async with pg_sessionmaker() as session:
        with pytest.raises(ValueError, match="transaction"):
            await append_event(
                session,
                key=KEY,
                execution_id="invoice-1",
                actor="settlement",
                event_type="TEST",
                payload={},
                ts=NOW,
            )


async def test_append_rejects_snapshot_isolation(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        with pytest.raises(ValueError, match="READ COMMITTED"):
            await append_event(
                session,
                key=KEY,
                execution_id="invoice-1",
                actor="settlement",
                event_type="TEST",
                payload={},
                ts=NOW,
            )
    assert await _events(pg_sessionmaker) == []


@pytest.mark.parametrize(
    "payload",
    [{1: "integer key"}, {"value": float("nan")}, {"value": float("inf")}, {"value": object()}],
)
async def test_invalid_json_payload_never_appends(pg_sessionmaker, payload):
    async with pg_sessionmaker() as session, session.begin():
        with pytest.raises(ValueError):
            await append_event(
                session,
                key=KEY,
                execution_id="invoice-1",
                actor="settlement",
                event_type="TEST",
                payload=payload,
                ts=NOW,
            )
    assert await _events(pg_sessionmaker) == []


async def test_append_rejects_an_already_corrupted_chain(pg_sessionmaker):
    await _append(pg_sessionmaker)
    async with pg_sessionmaker() as session, session.begin():
        row = (await session.scalars(select(ExecutionEvent))).one()
        row.payload = {"amount_cents": 9999}
    with pytest.raises(AuditIntegrityError):
        await _append(pg_sessionmaker, 1)
    assert len(await _events(pg_sessionmaker)) == 1


async def test_append_rechecks_persisted_rows_despite_session_cache(pg_sessionmaker):
    event = await _append(pg_sessionmaker)
    async with pg_sessionmaker() as session, session.begin():
        cached = await session.get(ExecutionEvent, event.event_seq)
        async with pg_sessionmaker() as other, other.begin():
            await other.execute(text("UPDATE execution_events SET payload = '{}'::jsonb"))
        assert cached.payload["amount_cents"] == 1234
        with pytest.raises(AuditIntegrityError):
            await append_event(
                session,
                key=KEY,
                execution_id="invoice-2",
                actor="settlement",
                event_type="TEST",
                payload={},
                ts=NOW,
            )
    assert len(await _events(pg_sessionmaker)) == 1


@given(st.integers(min_value=1, max_value=2**63 - 1).filter(lambda value: value != 1234))
def test_payload_amount_tampering_always_fails(amount):
    # Independently constructed fixed signed row; expected bytes documented in the contract.
    import hashlib
    import hmac

    wire = (
        b'{"actor":"settlement","event_seq":1,"event_type":"TEST",'
        b'"execution_id":"invoice-1","payload":{"amount_cents":1234},'
        b'"prev_hash":"0000000000000000000000000000000000000000000000000000000000000000",'
        b'"ts":"2026-10-26T12:00:00.000000Z"}'
    )
    signature = hmac.new(KEY, GENESIS.encode() + wire, hashlib.sha256).hexdigest()
    row = ExecutionEvent(
        event_seq=1,
        execution_id="invoice-1",
        actor="settlement",
        event_type="TEST",
        payload={"amount_cents": 1234},
        ts=NOW,
        prev_hash=GENESIS,
        hash=signature,
    )
    verify_chain([row], key=KEY, expected_head=signature)
    row.payload = copy.deepcopy(row.payload)
    row.payload["amount_cents"] = amount
    with pytest.raises(AuditIntegrityError):
        verify_chain([row], key=KEY, expected_head=signature)
