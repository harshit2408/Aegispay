"""R11 recovery gaps use durable Postgres checkpoints and the real approval ledger."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import func, select, text, update

from app.agents.provider import postgres_dsn
from app.decision.dispatch import DecisionConflict, Dispatcher, DispatchUnavailable
from app.models.core import Operator
from app.models.payables import Approval
from app.schemas.case import DecisionRequest
from tests.unit.test_decision_graph import (
    CONFIG,
    EXECUTION,
    graph_for,
    initial_vote,
    seeded_case,  # noqa: F401
)
from tests.unit.test_match import invoice


@pytest.fixture
async def dispatch_case(pg_url, pg_sessionmaker, seeded_case):  # noqa: F811
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        graph, _, reader, approve = graph_for(saver, pg_sessionmaker)
        dispatcher = Dispatcher(graph, pg_sessionmaker, postgres_dsn(pg_url))
        yield dispatcher, graph, reader, approve


async def vote(sessions):
    async with sessions() as session:
        return await session.get(Approval, "vote-1")


async def test_committed_vote_before_invoke_recovers_once(dispatch_case, pg_sessionmaker):
    dispatcher, graph, _, _ = dispatch_case
    await initial_vote(graph, pg_sessionmaker)
    original_invoke = graph.ainvoke
    graph.ainvoke = AsyncMock(wraps=original_invoke)
    await dispatcher.run(EXECUTION)
    assert (await vote(pg_sessionmaker)).consumed_at is not None
    assert isinstance(graph.ainvoke.call_args.args[0], Command)
    graph.ainvoke.reset_mock()
    await dispatcher.run(EXECUTION)
    graph.ainvoke.assert_not_awaited()


async def test_durable_receipt_consumed_after_dispatcher_crash(
    dispatch_case, pg_sessionmaker, monkeypatch
):
    dispatcher, graph, _, _ = dispatch_case
    await initial_vote(graph, pg_sessionmaker)
    consume = dispatcher._consume
    monkeypatch.setattr(dispatcher, "_consume", AsyncMock(side_effect=OSError("crash")))
    with pytest.raises(OSError, match="crash"):
        await dispatcher.run(EXECUTION)
    assert (await vote(pg_sessionmaker)).consumed_at is None
    graph.ainvoke = AsyncMock(wraps=graph.ainvoke)
    monkeypatch.setattr(dispatcher, "_consume", consume)
    await dispatcher.run(EXECUTION)
    assert (await vote(pg_sessionmaker)).consumed_at is not None
    graph.ainvoke.assert_not_awaited()


async def test_voided_vote_receipt_reconciles_without_replaying(dispatch_case, pg_sessionmaker):
    dispatcher, graph, reader, _ = dispatch_case
    binding = await initial_vote(graph, pg_sessionmaker)
    reader.get_invoice.return_value = invoice(status="PAID")
    await graph.ainvoke(
        Command(resume="vote-1"),
        {"configurable": {**CONFIG["configurable"], "resume_binding": binding}},
        durability="sync",
    )
    assert (await vote(pg_sessionmaker)).voided_at is not None
    graph.ainvoke = AsyncMock(wraps=graph.ainvoke)
    await dispatcher.recover_startup(dispatcher.run)
    current = await vote(pg_sessionmaker)
    assert current.voided_at is not None and current.consumed_at is not None
    graph.ainvoke.assert_not_awaited()


async def test_node_revocation_voids_vote_and_never_retries(dispatch_case, pg_sessionmaker):
    dispatcher, graph, _, approve = dispatch_case
    await initial_vote(graph, pg_sessionmaker)
    async with pg_sessionmaker.begin() as session:
        await session.execute(
            update(Operator).where(Operator.operator_id == "op-cfo").values(disabled=True)
        )
    with pytest.raises(DecisionConflict, match="manual review"):
        await dispatcher.run(EXECUTION)
    current = await vote(pg_sessionmaker)
    assert current.voided_at is not None and current.consumed_at is None
    graph.ainvoke = AsyncMock(wraps=graph.ainvoke)
    await dispatcher.run(EXECUTION)
    graph.ainvoke.assert_not_awaited()
    approve.assert_not_awaited()


async def test_foreign_checkpoint_is_not_an_ancestor(dispatch_case, pg_sessionmaker, monkeypatch):
    dispatcher, graph, _, approve = dispatch_case
    await initial_vote(graph, pg_sessionmaker)
    snapshot = await dispatcher.snapshot(EXECUTION)
    foreign = snapshot._replace(
        config={"configurable": {"thread_id": "thread-e1", "checkpoint_id": "foreign"}},
        parent_config=None,
    )
    monkeypatch.setattr(dispatcher, "snapshot", AsyncMock(return_value=foreign))
    graph.ainvoke = AsyncMock(wraps=graph.ainvoke)
    with pytest.raises(DecisionConflict, match="lineage"):
        await dispatcher.run(EXECUTION)
    assert (await vote(pg_sessionmaker)).voided_at is not None
    graph.ainvoke.assert_not_awaited()
    approve.assert_not_awaited()


async def test_repeated_browser_vote_is_idempotent_and_bound(dispatch_case, pg_sessionmaker):
    dispatcher, graph, _, _ = dispatch_case
    await graph.ainvoke({"execution_id": EXECUTION}, CONFIG, durability="sync")
    snapshot = await dispatcher.snapshot(EXECUTION)
    decision = DecisionRequest(
        action="APPROVE",
        reason="Reviewed trusted facts",
        gate_hash=snapshot.tasks[0].interrupts[0].value["gate_hash"],
    )
    ids = await asyncio.gather(
        dispatcher.decide(EXECUTION, "op-cfo", decision),
        dispatcher.decide(EXECUTION, "op-cfo", decision),
    )
    assert ids[0] == ids[1]
    async with pg_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Approval)) == 1
        current = await session.get(Approval, ids[0])
        assert current.consumed_at is not None
    changed = decision.model_copy(update={"reason": "Different decision reason"})
    with pytest.raises(DecisionConflict, match="already voted"):
        await dispatcher.decide(EXECUTION, "op-cfo", changed)


@pytest.mark.parametrize("task_error", [None, "transient DB failure"])
async def test_consumed_receipt_continues_ordinary_work_without_new_resume(
    dispatch_case, pg_sessionmaker, monkeypatch, task_error
):
    dispatcher, graph, _, _ = dispatch_case
    binding = await initial_vote(graph, pg_sessionmaker)
    async with pg_sessionmaker.begin() as session:
        current = await session.get(Approval, "vote-1")
        current.consumed_at = datetime.now(UTC)
    snapshot = SimpleNamespace(
        values={"execution_id": EXECUTION, "resume_receipts": {"vote-1": binding}},
        next=("match",),
        tasks=(SimpleNamespace(name="match", interrupts=(), error=task_error),),
    )
    monkeypatch.setattr(dispatcher, "snapshot", AsyncMock(return_value=snapshot))
    graph.ainvoke = AsyncMock()
    await dispatcher.recover_startup(dispatcher.run)
    graph.ainvoke.assert_awaited_once()
    assert graph.ainvoke.call_args.args[0] is None
    assert graph.ainvoke.call_args.args[1]["configurable"]["thread_id"] == "thread-e1"


async def test_session_lock_survives_app_commits_and_serializes_runs(
    dispatch_case, pg_sessionmaker
):
    dispatcher, _, _, _ = dispatch_case
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with dispatcher._locked(EXECUTION):
            async with pg_sessionmaker.begin() as session:
                await session.execute(text("SELECT 1"))
            entered.set()
            await release.wait()

    next_entered = asyncio.Event()

    async def second():
        await entered.wait()
        async with dispatcher._locked(EXECUTION):
            next_entered.set()

    one, two = asyncio.create_task(first()), asyncio.create_task(second())
    try:
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(next_entered.wait(), 0.1)
        release.set()
        await asyncio.gather(one, two)
        assert next_entered.is_set()
    finally:
        release.set()
        await asyncio.gather(one, two)


async def test_lost_session_lock_cancels_dispatch_before_more_work(dispatch_case, pg_sessionmaker):
    dispatcher, _, _, _ = dispatch_case
    entered = asyncio.Event()
    continued = False

    async def invocation():
        nonlocal continued
        async with dispatcher._locked(EXECUTION):
            entered.set()
            await asyncio.sleep(10)
            continued = True

    running = asyncio.create_task(invocation())
    try:
        await entered.wait()
        async with pg_sessionmaker.begin() as session:
            await session.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND application_name='aegis-dispatch'"
                )
            )
        with pytest.raises(DispatchUnavailable, match="lock was lost"):
            await asyncio.wait_for(running, 4)
        assert not continued
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
