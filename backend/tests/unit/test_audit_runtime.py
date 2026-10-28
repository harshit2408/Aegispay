"""R21 runtime: real PostgreSQL transactions and local bare Git, never hosted credentials."""

import asyncio
import csv
import io
import json
import os
import subprocess
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.audit import consumer as consumer_module
from app.audit.anchor import (
    AnchorConflict,
    AnchorUnavailable,
    GitAnchors,
    Snapshot,
    validate_remote,
)
from app.audit.consumer import AuditConsumer, run_forever
from app.audit.events import AuditIntegrityError, enqueue_event, verify_chain
from app.audit.export import export_events
from app.config import SettlementSettings
from app.models.payables import (
    AuditAnchor,
    AuditQueue,
    ExecutionEvent,
    InvoiceExecution,
    SettlementControl,
)
from app.settlement import transitions
from tests.seed import seed_approved, seed_world
from tests.unit.test_audit_events import GENESIS, KEY, NOW
from tests.unit.test_service_startup import settlement_values


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        check=True,
    ).stdout.strip()


@pytest.fixture
def anchors(tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--quiet")
    return GitAnchors(str(remote))


async def queue(sm, *, execution="e1", event="ANALYZED", actor="system", role=None):
    async with sm() as session, session.begin():
        if role:
            await session.execute(text("SET LOCAL ROLE " + role))
        return await enqueue_event(
            session,
            execution_id=execution,
            actor=actor,
            event_type=event,
            payload={"amount_cents": 48000},
            ts=NOW,
        )


async def rows(sm, model):
    async with sm() as session:
        return list(await session.scalars(select(model)))


async def test_git_roundtrip_compare_and_swap_and_restart(anchors):
    empty = await anchors.read()
    assert empty == Snapshot(None)
    first = await anchors.publish(empty, event_seq=0, head_hash=GENESIS, now=NOW)
    assert (await GitAnchors(anchors.remote).read()) == first
    with pytest.raises(AnchorConflict):
        await anchors.publish(empty, event_seq=1, head_hash="a" * 64, now=NOW)
    second = await anchors.publish(
        first, event_seq=1, head_hash="a" * 64, now=NOW + timedelta(hours=1)
    )
    assert first.checkpoint.external_ref in second.commits
    assert git(anchors.remote, "rev-list", "--count", "audit-anchors") == "2"


async def test_publication_rejects_invalid_or_regressing_checkpoint_before_writing(anchors):
    previous = await anchors.publish(await anchors.read(), event_seq=1, head_hash="a" * 64, now=NOW)
    for sequence, head, timestamp in [
        (-1, GENESIS, NOW),
        (True, GENESIS, NOW),
        (0, GENESIS, NOW),
        (1, "b" * 64, NOW),
        (2, "invalid", NOW),
        (2, "c" * 64, NOW.replace(tzinfo=None)),
    ]:
        with pytest.raises(ValueError):
            await anchors.publish(previous, event_seq=sequence, head_hash=head, now=timestamp)
    assert await anchors.read() == previous


@pytest.mark.parametrize(
    "remote",
    [
        "--upload-pack=evil",
        "ext::evil",
        "file:///tmp/a",
        "https://user:secret@example.com/a",
        "https://example.com/a?token=secret",
        "http://example.com/a",
    ],
)
def test_remote_config_rejects_shell_transports_and_embedded_credentials(remote):
    with pytest.raises(ValueError):
        validate_remote(remote)


def test_git_subprocess_has_only_dedicated_auth_and_safe_environment(monkeypatch, tmp_path):
    for name in (
        "TREASURY_CLIENT_SECRET",
        "VENDOR_CLIENT_SECRET",
        "LLM_API_KEY",
        "SESSION_SECRET",
        "AUDIT_HMAC_KEY",
        "GIT_CONFIG_PARAMETERS",
        "GIT_SSH_COMMAND",
        "HTTP_PROXY",
    ):
        monkeypatch.setenv(name, "must-not-reach-git")
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    GitAnchors("https://example.com/a.git", "dedicated-token")._git(str(tmp_path), "status")
    assert "must-not-reach-git" not in repr(captured)
    assert "dedicated-token" not in repr(captured["command"])
    assert captured["env"]["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "core.hooksPath=" + os.devnull in captured["command"]
    assert captured["timeout"] == 30 and captured.get("shell", False) is False


async def test_remote_symlink_and_executable_hook_are_never_checked_out(anchors, tmp_path):
    first = await anchors.publish(await anchors.read(), event_seq=0, head_hash=GENESIS, now=NOW)
    checkout = tmp_path / "malicious"
    checkout.mkdir()
    git(checkout, "init", "--quiet")
    git(checkout, "fetch", "--quiet", anchors.remote, "audit-anchors")
    git(checkout, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    protected = tmp_path / "protected"
    protected.write_text("must survive")
    # A Git symlink's blob is its target; even this valid JSON blob is never materialized.
    target = json.dumps({"event_seq": 0, "head_hash": GENESIS, "created_at": NOW.isoformat()})
    result = subprocess.run(
        ["git", "-C", str(checkout), "hash-object", "-w", "--stdin"],
        input=target,
        text=True,
        capture_output=True,
        check=True,
    )
    blob = result.stdout.strip()
    git(checkout, "update-index", "--add", "--cacheinfo", "120000," + blob + ",anchor.json")
    (checkout / "post-checkout").write_text("#!/bin/sh\nexit 99\n")
    (checkout / "post-checkout").chmod(0o755)
    git(checkout, "add", "post-checkout")
    git(
        checkout,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "malicious files",
    )
    git(checkout, "push", "--quiet", anchors.remote, "HEAD:refs/heads/audit-anchors")
    changed = await anchors.read()
    assert first.checkpoint.external_ref in changed.commits
    await anchors.publish(changed, event_seq=0, head_hash=GENESIS, now=NOW + timedelta(hours=1))
    assert protected.read_text() == "must survive"


async def test_queue_drain_under_actual_roles_is_atomic_and_ordered(pg_sessionmaker, anchors):
    await queue(pg_sessionmaker, event="UPLOADED", role="aegis_api_role")
    await queue(pg_sessionmaker, event="SETTLED", role="aegis_settlement_role")
    # A session factory which exercises the restricted role for every consumer transaction.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def restricted():
        async with pg_sessionmaker() as session:
            await session.execute(text("SET ROLE aegis_settlement_role"))
            await session.commit()
            try:
                yield session
            finally:
                await session.rollback()
                await session.execute(text("RESET ROLE"))
                await session.commit()

    worker = AuditConsumer(restricted, key=KEY, anchors=anchors)
    assert await worker.run_once(now=NOW) == 2
    signed = await rows(pg_sessionmaker, ExecutionEvent)
    assert [row.event_type for row in signed] == ["UPLOADED", "SETTLED"]
    assert await rows(pg_sessionmaker, AuditQueue) == []
    assert len(await rows(pg_sessionmaker, AuditAnchor)) == 1
    assert verify_chain(signed, key=KEY, expected_head=(await anchors.read()).checkpoint.head_hash)
    with pytest.raises(DBAPIError):
        async with pg_sessionmaker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE aegis_api_role"))
            await session.execute(text("DELETE FROM execution_events"))


async def test_signed_insert_failure_rolls_back_deletion_and_events(pg_sessionmaker, monkeypatch):
    await queue(pg_sessionmaker)
    await queue(pg_sessionmaker)
    append = consumer_module.append_event
    attempts = 0

    async def failed(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("injected crash")
        return await append(*args, **kwargs)

    monkeypatch.setattr(consumer_module, "append_event", failed)
    with pytest.raises(RuntimeError):
        await AuditConsumer(pg_sessionmaker, key=KEY, anchors=None).run_once(now=NOW)
    assert len(await rows(pg_sessionmaker, AuditQueue)) == 2
    assert await rows(pg_sessionmaker, ExecutionEvent) == []


async def test_drain_waits_for_prior_uncommitted_insert_and_preserves_sequence(pg_sessionmaker):
    assigned, release = asyncio.Event(), asyncio.Event()

    async def first():
        async with pg_sessionmaker() as session, session.begin():
            await enqueue_event(
                session, execution_id="first", actor="system", event_type="FIRST", payload={}
            )
            assigned.set()
            await release.wait()

    a = asyncio.create_task(first())
    await assigned.wait()
    await queue(pg_sessionmaker, event="SECOND")
    b = asyncio.create_task(AuditConsumer(pg_sessionmaker, key=KEY, anchors=None).run_once(now=NOW))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(b), 0.05)
    release.set()
    await asyncio.gather(a, b)
    await asyncio.gather(
        *(AuditConsumer(pg_sessionmaker, key=KEY, anchors=None).run_once(now=NOW) for _ in range(2))
    )
    assert [row.event_type for row in await rows(pg_sessionmaker, ExecutionEvent)] == [
        "FIRST",
        "SECOND",
    ]


async def test_audit_producers_do_not_invert_mandate_money_lock_order(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        await seed_world(session)
    queued, mandate_locked = asyncio.Event(), asyncio.Event()

    async def freeze_then_release():
        async with pg_sessionmaker() as session, session.begin():
            await enqueue_event(
                session, execution_id="freeze", actor="system", event_type="FROZEN", payload={}
            )
            queued.set()
            await mandate_locked.wait()
            await session.execute(text("SELECT policy_id FROM mandate_policies FOR UPDATE"))

    async def reserve_then_send():
        await queued.wait()
        async with pg_sessionmaker() as session, session.begin():
            await session.execute(text("SELECT policy_id FROM mandate_policies FOR UPDATE"))
            mandate_locked.set()
            await enqueue_event(
                session,
                execution_id="send",
                actor="system",
                event_type="PAYOUT_SENDING",
                payload={},
            )

    await asyncio.wait_for(asyncio.gather(freeze_then_release(), reserve_then_send()), 2)
    assert len(await rows(pg_sessionmaker, AuditQueue)) == 2


async def test_checkpoint_hourly_and_each_settled_and_no_db_lock_over_git(pg_sessionmaker, anchors):
    await queue(pg_sessionmaker)
    worker = AuditConsumer(pg_sessionmaker, key=KEY, anchors=anchors)
    publish = anchors.publish
    # The precise shared constant, rather than a second unrelated advisory lock.
    from app.audit.events import _CHAIN_LOCK

    async def checked_publish(*args, **kwargs):
        async with pg_sessionmaker() as session, session.begin():
            assert await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": _CHAIN_LOCK}
            )
        return await publish(*args, **kwargs)

    anchors.publish = checked_publish
    await worker.run_once(now=NOW)
    first = await anchors.read()
    await queue(pg_sessionmaker, event="ANALYZING")
    await worker.run_once(now=NOW + timedelta(minutes=1))
    assert await anchors.read() == first
    await queue(pg_sessionmaker, event="SETTLED")
    await worker.run_once(now=NOW + timedelta(minutes=2))
    second = await anchors.read()
    assert second.checkpoint.event_seq > first.checkpoint.event_seq
    await worker.run_once(now=NOW + timedelta(minutes=62))
    assert (await anchors.read()).checkpoint.external_ref != second.checkpoint.external_ref


async def test_push_success_receipt_failure_recovers_on_restart(
    pg_sessionmaker, anchors, monkeypatch
):
    await queue(pg_sessionmaker)
    receipt = consumer_module._receipt

    async def failed(session, snapshot):
        if snapshot.checkpoint:
            raise RuntimeError("DB died after push")
        return await receipt(session, snapshot)

    monkeypatch.setattr(consumer_module, "_receipt", failed)
    with pytest.raises(RuntimeError):
        await AuditConsumer(pg_sessionmaker, key=KEY, anchors=anchors).run_once(now=NOW)
    published = await anchors.read()
    assert published.checkpoint and await rows(pg_sessionmaker, AuditAnchor) == []
    monkeypatch.setattr(consumer_module, "_receipt", receipt)
    await AuditConsumer(pg_sessionmaker, key=KEY, anchors=GitAnchors(anchors.remote)).run_once(
        now=NOW
    )
    assert len(await rows(pg_sessionmaker, AuditAnchor)) == 1
    assert await anchors.read() == published


async def test_truncation_and_remote_branch_regression_are_detected(pg_sessionmaker, anchors):
    await queue(pg_sessionmaker)
    worker = AuditConsumer(pg_sessionmaker, key=KEY, anchors=anchors)
    await worker.run_once(now=NOW)
    first = await anchors.read()
    await queue(pg_sessionmaker, event="SETTLED")
    await worker.run_once(now=NOW + timedelta(minutes=1))
    git(anchors.remote, "update-ref", "refs/heads/audit-anchors", first.checkpoint.external_ref)
    with pytest.raises(AuditIntegrityError, match="regressed"):
        await worker.run_once(now=NOW)
    control = (await rows(pg_sessionmaker, SettlementControl))[0]
    assert control.halted and control.halted_reason == "AUDIT_INTEGRITY_FAILED"
    assert control.changed_by == "system:audit"
    # Remove the remote entirely while DB receipts still exist.
    git(anchors.remote, "update-ref", "-d", "refs/heads/audit-anchors")
    with pytest.raises(AuditIntegrityError, match="disappeared"):
        await worker.run_once(now=NOW)


async def test_export_verifies_global_chain_before_filter_and_excludes_pending(
    pg_sessionmaker, anchors
):
    await queue(pg_sessionmaker, execution="other")
    await queue(pg_sessionmaker, actor='=HYPERLINK("evil")')
    worker = AuditConsumer(pg_sessionmaker, key=KEY, anchors=anchors)
    await worker.run_once(now=NOW)
    await queue(pg_sessionmaker)
    await worker.run_once(now=NOW + timedelta(minutes=1))
    value = json.loads(await export_events(pg_sessionmaker, anchors, key=KEY, execution_id="e1"))
    assert len(value["events"]) == 1 and value["pending_unanchored_events"] == 1
    parsed = list(
        csv.DictReader(
            io.StringIO(
                await export_events(
                    pg_sessionmaker, anchors, key=KEY, execution_id="e1", format="csv"
                )
            )
        )
    )
    assert parsed[0]["actor"].startswith("'=HYPERLINK")
    empty = list(
        csv.DictReader(
            io.StringIO(
                await export_events(
                    pg_sessionmaker, anchors, key=KEY, execution_id="missing", format="csv"
                )
            )
        )
    )
    assert empty[0]["event_seq"] == "" and empty[0]["anchor_commit"]
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("DELETE FROM execution_events WHERE execution_id='other'"))
    with pytest.raises(AuditIntegrityError):
        await export_events(pg_sessionmaker, anchors, key=KEY, execution_id="e1")


async def test_export_detects_suffix_truncation_at_external_checkpoint(pg_sessionmaker, anchors):
    await queue(pg_sessionmaker)
    worker = AuditConsumer(pg_sessionmaker, key=KEY, anchors=anchors)
    await worker.run_once(now=NOW)
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("DELETE FROM execution_events"))
        await session.execute(text("DELETE FROM audit_anchors"))
    with pytest.raises(AuditIntegrityError):
        await export_events(pg_sessionmaker, anchors, key=KEY, execution_id="e1")


async def test_replay_export_is_explicit_opt_in(pg_sessionmaker, anchors):
    from tests.seed import make_execution

    async with pg_sessionmaker() as session, session.begin():
        await seed_world(session)
        session.add(make_execution("replay", replayed=True))
    await queue(pg_sessionmaker, execution="replay")
    await AuditConsumer(pg_sessionmaker, key=KEY, anchors=anchors).run_once(now=NOW)
    normal = json.loads(
        await export_events(pg_sessionmaker, anchors, key=KEY, execution_id="replay")
    )
    explicit = json.loads(
        await export_events(
            pg_sessionmaker, anchors, key=KEY, execution_id="replay", include_replay=True
        )
    )
    assert normal["events"] == [] and len(explicit["events"]) == 1


async def test_transition_audit_and_rollback_share_the_money_transaction(pg_sessionmaker):
    async with pg_sessionmaker() as session, session.begin():
        await seed_world(session)
        await seed_approved(session)
    with pytest.raises(RuntimeError):
        async with pg_sessionmaker() as session, session.begin():
            execution = await session.scalar(select(InvoiceExecution).with_for_update())
            await transitions.apply(session, execution, "PAYOUT_SENDING")
            raise RuntimeError("rollback")
    assert await rows(pg_sessionmaker, AuditQueue) == []
    assert (await rows(pg_sessionmaker, InvoiceExecution))[0].status == "APPROVED_FOR_PAYMENT"
    async with pg_sessionmaker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE aegis_settlement_role"))
        execution = await session.scalar(select(InvoiceExecution).with_for_update())
        await transitions.apply(session, execution, "PAYOUT_SENDING")
    queued = await rows(pg_sessionmaker, AuditQueue)
    assert len(queued) == 1 and queued[0].event_type == "PAYOUT_SENDING"
    assert queued[0].payload == {
        "from_status": "APPROVED_FOR_PAYMENT",
        "to_status": "PAYOUT_SENDING",
        "row_version": 1,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"audit_hmac_key": "short"},
        {"audit_hmac_key": ""},
        {"audit_anchor_repo": "https://user:password@example.com/repo"},
        {"audit_anchor_token": "token", "audit_anchor_repo": ""},
    ],
)
def test_audit_settings_fail_closed(overrides):
    with pytest.raises(ValidationError):
        SettlementSettings(_env_file=None, **settlement_values(**overrides))


async def test_anchor_absence_and_transport_errors_never_export_unverified_data(
    pg_sessionmaker, anchors
):
    with pytest.raises(AnchorUnavailable):
        await export_events(pg_sessionmaker, anchors, key=KEY, execution_id="e1")
    await queue(pg_sessionmaker)
    await AuditConsumer(pg_sessionmaker, key=KEY, anchors=None).run_once(now=NOW)
    assert len(await rows(pg_sessionmaker, ExecutionEvent)) == 1
    assert await rows(pg_sessionmaker, AuditAnchor) == []


async def test_integrity_failure_stops_loop_and_transport_failure_retries(caplog):
    class Broken:
        anchors = None

        async def run_once(self):
            raise AuditIntegrityError("private exception text")

    with pytest.raises(AuditIntegrityError):
        await run_forever(Broken(), asyncio.Event())
    assert "audit_integrity_failed" in caplog.text and "private exception text" not in caplog.text
    stop = asyncio.Event()

    class Offline:
        anchors = True

        async def run_once(self):
            stop.set()
            raise AnchorUnavailable("secret transport URL")

    await run_forever(Offline(), stop)
    assert "secret transport URL" not in caplog.text


def test_cli_refuses_unexpected_errors_without_credential_traceback(monkeypatch, capsys):
    from app.audit import export

    async def failed(args):
        raise RuntimeError("postgres://private-password@internal/")

    monkeypatch.setattr(export, "_run", failed)
    monkeypatch.setattr("sys.argv", ["audit-export", "e1"])
    with pytest.raises(SystemExit) as error:
        export.main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "private-password" not in captured.err + captured.out
    assert "Audit export refused" in captured.err
