"""Serialize case runs and reconcile durable LangGraph approval receipts (R11)."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from uuid import uuid4

from langgraph.types import Command
from psycopg import AsyncConnection
from sqlalchemy import select

from app.audit.events import enqueue_event
from app.models.core import Operator
from app.models.payables import Approval, InvoiceDocument, InvoiceExecution


class DecisionConflict(ValueError):
    pass


class DispatchUnavailable(RuntimeError):
    pass


def _binding(approval):
    return {
        "execution_id": approval.execution_id,
        "checkpoint_id": approval.resume_checkpoint_id,
        "interrupt_id": approval.resume_interrupt_id,
        "gate_hash": approval.gate_hash,
    }


def _pending(snapshot, execution_id):
    if len(snapshot.tasks) != 1:
        raise DecisionConflict("Case is awaiting a fresh review")
    task = snapshot.tasks[0]
    if task.name != "human_review" or task.error or len(task.interrupts) != 1:
        raise DecisionConflict("Case is awaiting a fresh review")
    interrupt = task.interrupts[0]
    if interrupt.value.get("execution_id") != execution_id:
        raise DecisionConflict("Case review binding is unavailable")
    return interrupt, snapshot.config["configurable"]["checkpoint_id"]


class Dispatcher:
    def __init__(self, graph, sessions, lock_dsn):
        self.graph = graph
        self.sessions = sessions
        self.lock_dsn = lock_dsn

    @asynccontextmanager
    async def _locked(self, execution_id):
        # Use a dedicated unpooled connection. Closing it releases the session lock.
        async with await AsyncConnection.connect(self.lock_dsn, autocommit=True) as conn:
            await conn.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 914))", (execution_id,)
            )
            owner = asyncio.current_task()
            lost = False

            async def watch():
                nonlocal lost
                while True:
                    await asyncio.sleep(0.25)
                    try:
                        async with asyncio.timeout(2):
                            await conn.execute("SELECT 1")
                    except Exception:
                        lost = True
                        owner.cancel()
                        return

            watcher = asyncio.create_task(watch())
            try:
                yield
            except asyncio.CancelledError:
                if lost:
                    raise DispatchUnavailable("Case dispatch lock was lost") from None
                raise
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher

    async def _config(self, execution_id):
        async with self.sessions() as session:
            row = await session.get(InvoiceExecution, execution_id)
            if row is None:
                raise DecisionConflict("Case unavailable")
            return {"configurable": {"thread_id": row.langgraph_thread_id}}

    async def snapshot(self, execution_id):
        return await self.graph.aget_state(await self._config(execution_id))

    async def run(self, execution_id):
        async with self._locked(execution_id):
            await self._recover(execution_id)
            snapshot = await self.snapshot(execution_id)
            config = await self._config(execution_id)
            if not snapshot.values:
                await self.graph.ainvoke({"execution_id": execution_id}, config, durability="sync")
            elif snapshot.next and all(
                not (task.interrupts or task.error) for task in snapshot.tasks
            ):
                await self.graph.ainvoke(None, config, durability="sync")

    async def decide(self, execution_id, actor_id, decision):
        async with self._locked(execution_id):
            await self._recover(execution_id)
            snapshot = await self.snapshot(execution_id)
            async with self.sessions.begin() as session:
                case = await session.get(InvoiceExecution, execution_id, with_for_update=True)
                actor = await session.get(Operator, actor_id)
                if case is None or case.status != "FROZEN":
                    raise DecisionConflict("Case is unavailable for review")
                doc = await session.get(InvoiceDocument, case.document_id)
                if actor is None or actor.disabled or actor.role not in {"controller", "cfo"}:
                    raise DecisionConflict("Operator is ineligible")
                if doc.uploaded_by == actor_id:
                    raise DecisionConflict("Uploader cannot review their own case")
                interrupt, checkpoint = _pending(snapshot, execution_id)
                gate = interrupt.value
                if (
                    decision.gate_hash != gate["gate_hash"]
                    or decision.action not in gate["allowed_actions"]
                ):
                    raise DecisionConflict("Case changed; review again")
                approval = await session.scalar(
                    select(Approval).where(
                        Approval.execution_id == execution_id,
                        Approval.actor == actor_id,
                        Approval.gate_hash == decision.gate_hash,
                    )
                )
                if approval is not None:
                    raise DecisionConflict("This operator already voted on the current gate")
                approval = Approval(
                    approval_id=str(uuid4()),
                    execution_id=execution_id,
                    actor=actor_id,
                    action=decision.action,
                    reason=decision.reason,
                    gate_hash=decision.gate_hash,
                    resume_checkpoint_id=checkpoint,
                    resume_interrupt_id=interrupt.id,
                )
                session.add(approval)
                await enqueue_event(
                    session,
                    execution_id=execution_id,
                    actor=actor_id,
                    event_type="DECISION_RECORDED",
                    payload={
                        "approval_id": approval.approval_id,
                        "action": approval.action,
                        "gate_hash": approval.gate_hash,
                    },
                )
            await self._resume(approval, snapshot)
            return approval.approval_id

    async def _recover(self, execution_id):
        async with self.sessions() as session:
            pending = (
                await session.scalars(
                    select(Approval)
                    .where(
                        Approval.execution_id == execution_id,
                        Approval.voided_at.is_(None),
                        Approval.consumed_at.is_(None),
                    )
                    .order_by(Approval.created_at)
                )
            ).all()
        for approval in pending:
            await self._resume(approval, await self.snapshot(execution_id))

    async def _resume(self, approval, snapshot):
        execution_id = approval.execution_id
        binding = _binding(approval)
        if snapshot.values.get("resume_receipts", {}).get(approval.approval_id) != binding:
            try:
                interrupt, checkpoint = _pending(snapshot, execution_id)
            except DecisionConflict:
                await self._void(approval, "Pending checkpoint unavailable")
                raise
            if (
                interrupt.id != approval.resume_interrupt_id
                or interrupt.value["gate_hash"] != approval.gate_hash
            ):
                await self._void(approval, "Pending checkpoint changed")
                raise DecisionConflict("Case changed; review again")
            if checkpoint != approval.resume_checkpoint_id:
                original_config = await self._config(execution_id)
                original_config["configurable"]["checkpoint_id"] = approval.resume_checkpoint_id
                original = await self.graph.aget_state(original_config)
                try:
                    original_interrupt, _ = _pending(original, execution_id)
                except DecisionConflict:
                    await self._void(approval, "Original checkpoint unavailable")
                    raise
                if original_interrupt.id != interrupt.id:
                    await self._void(approval, "Original interrupt changed")
                    raise DecisionConflict("Case changed; review again")
            config = await self._config(execution_id)
            config["configurable"]["resume_binding"] = binding
            try:
                await self.graph.ainvoke(
                    Command(resume=approval.approval_id), config, durability="sync"
                )
            except ValueError:
                latest = await self.snapshot(execution_id)
                if latest.values.get("resume_receipts", {}).get(approval.approval_id) != binding:
                    await self._void(approval, "Graph rejected pending approval")
                    raise DecisionConflict("Case requires manual review") from None
            snapshot = await self.snapshot(execution_id)
        if snapshot.values.get("resume_receipts", {}).get(approval.approval_id) != binding:
            raise DispatchUnavailable("Approval receipt is unavailable")
        async with self.sessions.begin() as session:
            current = await session.get(Approval, approval.approval_id, with_for_update=True)
            if current.consumed_at is None:
                current.consumed_at = datetime.now(UTC)

    async def _void(self, approval, reason):
        async with self.sessions.begin() as session:
            current = await session.get(Approval, approval.approval_id, with_for_update=True)
            if current.consumed_at is None and current.voided_at is None:
                current.voided_at = datetime.now(UTC)
                await enqueue_event(
                    session,
                    execution_id=approval.execution_id,
                    actor="system",
                    event_type="DECISION_VOIDED",
                    payload={"approval_id": approval.approval_id, "reason": reason},
                )
