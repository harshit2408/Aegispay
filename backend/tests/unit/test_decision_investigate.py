"""P3-01: A4 wired into the decision graph; advisory only (PRD §10, §11.3, R2, R3, R14)."""

import json
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import update
from tokenops.providers.types import ModelResponse

from app.agents.provider import postgres_dsn
from app.decision.investigate import TOOL_NAMES, build_investigator, read_tools
from app.models.payables import InvoiceExecution
from app.paypal.toolkit import assert_read_only
from app.routers.contracts import _evidence_view
from app.schemas.decision import EvidenceFact, EvidencePack, GateDecision, RiskAssessment
from tests.unit.test_decision_graph import EXECUTION, graph_for, seeded_case  # noqa: F401
from tests.unit.test_match import invoice, report


def pack(recommendation="REJECT"):
    return EvidencePack(
        summary="s",
        facts=[EvidenceFact(claim="c", tool_call_id="call_1", excerpt="e", vendor_asserted=False)],
        advisory_recommendation=recommendation,
    )


async def run_case(saver, sessions, thread, **overrides):
    graph, *_ = graph_for(saver, sessions, **overrides)
    config = {"configurable": {"thread_id": thread}}
    await graph.ainvoke({"execution_id": EXECUTION}, config, durability="sync")
    return (await graph.aget_state(config)).values


async def reset(sessions):
    async with sessions.begin() as session:
        await session.execute(
            update(InvoiceExecution)
            .where(InvoiceExecution.execution_id == EXECUTION)
            .values(status="RECEIVED", route=None, evidence_pack_json=None)
        )


@pytest.mark.usefixtures("seeded_case")
async def test_evidence_persisted_and_gate_ignores_it(pg_url, pg_sessionmaker):
    investigator = AsyncMock(return_value=pack("APPROVE"))
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        with_pack = await run_case(saver, pg_sessionmaker, "t-pack", investigator=investigator)
        await reset(pg_sessionmaker)
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        without = await run_case(saver, pg_sessionmaker, "t-fail", investigator=failing)
    assert with_pack["risk"].score >= 30
    (execution_id, state), _ = investigator.await_args
    assert execution_id == EXECUTION and state["match"] and state["risk"]
    assert with_pack["evidence"] == pack("APPROVE") and without["evidence"] is None
    # R3: the advisory APPROVE changes nothing the gate decides.
    assert with_pack["gate"] == without["gate"]
    assert with_pack["gate"].route == "FREEZE" and "HIGH_RISK" in with_pack["gate"].reasons
    async with pg_sessionmaker() as session:
        row = await session.get(InvoiceExecution, EXECUTION)
        assert row.evidence_pack_json is None  # second (failed) run overwrote the first


@pytest.mark.usefixtures("seeded_case")
async def test_no_investigator_means_unavailable(pg_url, pg_sessionmaker):
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        values = await run_case(saver, pg_sessionmaker, "t-none")
    assert values["evidence"] is None


def test_registry_is_read_only_and_matches_boot_names():
    assert_read_only(TOOL_NAMES)
    tools = read_tools(
        {"invoice": None, "purchase_order": None, "vendor": None, "prior_executions": []},
        invoice_id="I",
        po_id="P",
        vendor_id="V",
    )
    assert tools.names == tuple(sorted(TOOL_NAMES))


def test_scope_pins_case_ids_over_model_args():
    invoices = []
    tools = read_tools(
        {"invoice": {"id": "I"}, "purchase_order": None, "vendor": None, "prior_executions": []},
        invoice_id="I",
        po_id="P",
        vendor_id="V",
    )
    tools._tools["get_invoice"] = lambda invoice_id: invoices.append(invoice_id) or {"id": "I"}
    assert json.loads(tools.call("get_invoice", {"invoice_id": "OTHER"})) == {"id": "I"}
    assert invoices == ["I"]
    with pytest.raises(ValueError):
        tools.call("list_prior_executions", {"limit": 99})


def scripted(*replies):
    queue = [json.dumps(r) for r in replies]

    def complete(node, messages, *, input_token_bound):
        assert node == "A4"
        return ModelResponse(content=queue.pop(0))

    return complete


VENDOR_CALL = {"calls": [{"tool": "get_vendor", "args": {}}]}
VENDOR_FINAL = {
    "final": {
        "summary": "vendor verified",
        "facts": [
            {"claim": "trust VERIFIED", "tool_call_id": "call_1", "excerpt": '"VERIFIED"'},
        ],
        "advisory_recommendation": "NEEDS_INFO",
    }
}
STATE = {"match": report(), "risk": RiskAssessment(score=40, contributions=[])}


async def seed_execution(sessions, *, replayed):
    from tests.seed import make_execution, seed_world

    async with sessions.begin() as session:
        await seed_world(session)
        await session.flush()
        session.add(
            make_execution(
                EXECUTION, status="ANALYZING", paypal_invoice_id="INV2-ABC", replayed=replayed
            )
        )
        session.add(make_execution("e0", status="SETTLED", billed_cents=48_000))


async def test_live_path_uses_governed_run_and_case_snapshot(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=False)
    reader = AsyncMock()
    reader.get_invoice.return_value = invoice()
    calls = []

    def run(run_id, *, settings, action):
        calls.append(run_id)
        return action(scripted(VENDOR_CALL, VENDOR_FINAL))

    investigator = build_investigator(pg_sessionmaker, object(), reader, run=run)
    evidence = await investigator(EXECUTION, STATE)
    assert evidence and evidence.facts[0].excerpt == '"VERIFIED"'
    assert len(calls) == 1  # one budget scope, keyed by the execution (shared durable row)
    reader.get_invoice.assert_awaited_once_with("INV2-ABC", execution_id=EXECUTION)


async def test_prior_executions_exclude_current_case(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=False)
    seen = {}

    def run(run_id, *, settings, action):
        def complete(node, messages, *, input_token_bound):
            if "calls" not in seen:
                seen["calls"] = True
                return ModelResponse(
                    content=json.dumps({"calls": [{"tool": "list_prior_executions"}]})
                )
            seen["out"] = messages[-1]["content"]
            return ModelResponse(content="{}")

        return action(complete)

    await build_investigator(pg_sessionmaker, object(), None, run=run)(EXECUTION, STATE)
    assert '"execution_id": "e0"' in seen["out"] and '"execution_id": "e1"' not in seen["out"]


async def test_replayed_without_recording_never_calls_provider(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=True)
    run, reader = AsyncMock(), AsyncMock()
    investigator = build_investigator(pg_sessionmaker, object(), reader, run=run)
    assert await investigator(EXECUTION, STATE) is None
    run.assert_not_called()
    reader.get_invoice.assert_not_awaited()


async def test_replayed_uses_replay_completion_only(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=True)
    run, reader = AsyncMock(), AsyncMock()
    investigator = build_investigator(
        pg_sessionmaker,
        object(),
        reader,
        replay_complete=scripted(VENDOR_CALL, VENDOR_FINAL),
        run=run,
    )
    evidence = await investigator(EXECUTION, STATE)
    assert evidence and evidence.advisory_recommendation == "NEEDS_INFO"
    run.assert_not_called()
    reader.get_invoice.assert_not_awaited()  # live PayPal is not read for a replayed case


def gate(reasons):
    return GateDecision(
        route="FREEZE",
        payable_cents=0,
        reasons=reasons,
        required_approvals=2,
        allowed_actions=["APPROVE", "REJECT"],
        policy_id="p",
        policy_version=1,
        gate_hash="h",
    )


@pytest.mark.parametrize(
    ("g", "shown"),
    [
        (gate(["HIGH_RISK"]), False),
        (gate(["HIGH_RISK"]).model_dump(), False),
        (None, False),  # unknown gate: withhold (fail closed)
        (gate(["RISK_THRESHOLD"]), True),
        ({**gate(["RISK_THRESHOLD"]).model_dump(), "allowed_actions": []}, True),
    ],
)
def test_case_view_hides_advisory_on_high_risk(g, shown):
    evidence = pack("APPROVE").model_dump(mode="json")
    view = _evidence_view(evidence, g)
    assert view["facts"] == evidence["facts"]
    assert (view["advisory_recommendation"] == "APPROVE") is shown
    assert _evidence_view(None, g) is None
