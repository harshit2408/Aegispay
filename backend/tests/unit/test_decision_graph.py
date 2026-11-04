from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import update

from app.agents.extractor import ExtractionResult
from app.agents.provider import postgres_dsn
from app.decision.graph import ResumeRejected, build_graph
from app.models.core import GoodsReceipt, MandatePolicy, VendorMaster
from app.models.payables import Approval
from app.schemas.decision import ForensicsReport
from app.settlement.transitions import apply
from tests.seed import make_execution, seed_world
from tests.unit.test_match import extraction, invoice
from tests.unit.test_policy_gate import policy

EXECUTION = "e1"
CONFIG = {"configurable": {"thread_id": "thread-e1"}}


@pytest.fixture
async def seeded_case(pg_sessionmaker, request):
    now = datetime.now(UTC)
    compiled = policy(version=2)
    async with pg_sessionmaker.begin() as session:
        await seed_world(session)
        await session.execute(update(MandatePolicy).values(is_active=False))
        await session.execute(update(VendorMaster).values(onboarded_at=now - timedelta(days=90)))
        session.add_all(
            [
                MandatePolicy(
                    policy_id=compiled.policy_id,
                    version=2,
                    raw_nl_prompt="test",
                    compiled_json=compiled.model_dump(),
                    max_single_invoice_cents=compiled.max_single_invoice_cents,
                    daily_cap_cents=compiled.daily_cap_cents,
                    created_by="op-ctl",
                    activated_by="op-cfo",
                    activated_at=now,
                    is_active=True,
                ),
                GoodsReceipt(
                    grn_id="grn-1",
                    po_id="PO-1042",
                    received_qty=100,
                    accepted_qty=100,
                    rejected_qty=0,
                    recorded_by="op-rcv",
                    source="SEEDED",
                    received_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.flush()
        session.add(
            make_execution(
                EXECUTION,
                status="RECEIVED",
                policy_version=2,
                paypal_invoice_id="INV2-ABC",
                invoice_number_norm="INV1042",
                replayed=getattr(request, "param", False),
            )
        )


def graph_for(saver, sessions, **overrides):
    report = ForensicsReport(
        detector_enabled=True,
        hidden_segments=[],
        classifier_label="SUSPICIOUS",
        classifier_rationale="suspicious document",
        detector_version="test",
    )
    analyze = AsyncMock(
        return_value=(ExtractionResult(extraction=extraction(), freeze_reason=None), report)
    )
    reader = AsyncMock()
    reader.get_invoice.return_value = invoice()
    approve = AsyncMock(return_value=True)
    graph = build_graph(
        saver,
        **{
            "sessions": sessions,
            "analyze": analyze,
            "reader": reader,
            "approve": approve,
            "transition": apply,
            **overrides,
        },
    )
    return graph, analyze, reader, approve


async def initial_vote(graph, sessions):
    await graph.ainvoke({"execution_id": EXECUTION}, CONFIG, durability="sync")
    snapshot = await graph.aget_state(CONFIG)
    (review,) = snapshot.tasks
    assert review.name == "human_review"
    (pause,) = review.interrupts
    assert pause.value["required_approvals"] == 2
    binding = {
        "execution_id": EXECUTION,
        "checkpoint_id": snapshot.config["configurable"]["checkpoint_id"],
        "interrupt_id": pause.id,
        "gate_hash": pause.value["gate_hash"],
    }
    async with sessions.begin() as session:
        session.add(
            Approval(
                approval_id="vote-1",
                execution_id=EXECUTION,
                actor="op-cfo",
                action="APPROVE",
                reason="Reviewed trusted facts",
                gate_hash=binding["gate_hash"],
                resume_checkpoint_id=binding["checkpoint_id"],
                resume_interrupt_id=binding["interrupt_id"],
            )
        )
    return binding


async def test_resume_persists_receipt_then_rereads_invoice(pg_url, pg_sessionmaker, seeded_case):
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        graph, analyze, reader, approve = graph_for(saver, pg_sessionmaker)
        binding = await initial_vote(graph, pg_sessionmaker)
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        graph, analyze, reader, approve = graph_for(saver, pg_sessionmaker)
        reader.get_invoice.return_value = invoice(status="PAID")
        config = {"configurable": {**CONFIG["configurable"], "resume_binding": binding}}
        await graph.ainvoke(Command(resume="vote-1"), config, durability="sync")
        latest = await graph.aget_state(CONFIG)
        assert latest.values["resume_receipts"] == {"vote-1": binding}
        assert latest.values["gate"].route == "FREEZE"
        assert latest.values["gate"].gate_hash != binding["gate_hash"]
        assert "UNVERIFIED_SOURCE" in latest.values["gate"].reasons
        assert latest.tasks[0].interrupts[0].id != binding["interrupt_id"]
        analyze.assert_not_awaited()
        reader.get_invoice.assert_awaited_once()
        approve.assert_not_awaited()
    async with pg_sessionmaker() as session:
        vote = await session.get(Approval, "vote-1")
        assert vote.voided_at is not None and vote.consumed_at is None


async def test_wrong_interrupt_refuses_approval_at_node(pg_url, pg_sessionmaker, seeded_case):
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        graph, _, reader, approve = graph_for(saver, pg_sessionmaker)
        binding = await initial_vote(graph, pg_sessionmaker)
        wrong = {**binding, "interrupt_id": "other"}
        config = {"configurable": {**CONFIG["configurable"], "resume_binding": wrong}}
        with pytest.raises(ResumeRejected, match="pending gate"):
            await graph.ainvoke(Command(resume="vote-1"), config, durability="sync")
        latest = await graph.aget_state(CONFIG)
        assert not latest.values.get("resume_receipts")
        reader.get_invoice.assert_awaited_once()
        approve.assert_not_awaited()


@pytest.mark.parametrize("seeded_case", [True], indirect=True)
async def test_replay_without_recorded_reader_cannot_use_live_vendor(
    pg_url, pg_sessionmaker, seeded_case
):
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        graph, _, reader, approve = graph_for(saver, pg_sessionmaker)
        await graph.ainvoke({"execution_id": EXECUTION}, CONFIG, durability="sync")
        latest = await graph.aget_state(CONFIG)
        assert latest.values["gate"].reasons == ["SOURCE_READER_UNAVAILABLE"]
        assert latest.values["gate"].allowed_actions == []
        reader.get_invoice.assert_not_awaited()
        approve.assert_not_awaited()
