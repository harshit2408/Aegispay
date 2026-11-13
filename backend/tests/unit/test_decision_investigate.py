"""P3-01: A4 wired into the decision graph; advisory only (PRD §10, §11.3, R2, R3, R14)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import update
from tokenops.framework.core import Action, ActionKind
from tokenops.providers.types import ModelResponse

import app.decision.investigate as wiring
from app.agents import Halt
from app.agents.extractor import ExtractionResult
from app.agents.provider import postgres_dsn
from app.decision.investigate import TOOL_NAMES, build_investigator, read_tools
from app.main import app
from app.models.payables import InvoiceExecution
from app.paypal.toolkit import assert_read_only, is_vendor_asserted
from app.routers.contracts import _evidence_view
from app.schemas.decision import (
    EvidenceFact,
    EvidencePack,
    ForensicsReport,
    GateDecision,
    RiskAssessment,
)
from tests.seed import PAYER_ID, make_execution, seed_world
from tests.unit import test_case_settlement_view as case_view_module
from tests.unit.test_case_settlement_view import seed_settled
from tests.unit.test_decision_graph import EXECUTION, graph_for, seeded_case  # noqa: F401
from tests.unit.test_match import extraction, report

case = case_view_module.case  # fixture: GET /api/executions/e1 as op-ctl


def pack(recommendation="REJECT"):
    return EvidencePack(
        summary="s",
        facts=[EvidenceFact(claim="c", tool_call_id="call_1", excerpt="e", vendor_asserted=False)],
        advisory_recommendation=recommendation,
    )


def analyze_with(label):
    forensics = ForensicsReport(
        detector_enabled=True,
        hidden_segments=[],
        classifier_label=label,
        classifier_rationale="test",
        detector_version="test",
    )
    return AsyncMock(
        return_value=(ExtractionResult(extraction=extraction(), freeze_reason=None), forensics)
    )


async def run_case(saver, sessions, thread, label="INJECTION", **overrides):
    # INJECTION scores 60, so the case reaches the investigate edge (risk >= 30).
    graph, *_ = graph_for(saver, sessions, analyze=analyze_with(label), **overrides)
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


async def stored(sessions):
    async with sessions() as session:
        return await session.get(InvoiceExecution, EXECUTION)


@pytest.mark.usefixtures("seeded_case")
async def test_evidence_persisted_and_gate_ignores_it(pg_url, pg_sessionmaker):
    investigator = AsyncMock(return_value=pack("APPROVE"))
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        with_pack = await run_case(saver, pg_sessionmaker, "t-pack", investigator=investigator)
        row = await stored(pg_sessionmaker)
        assert row.status == "FROZEN"
        assert row.evidence_pack_json == pack("APPROVE").model_dump(mode="json")
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
    assert (await stored(pg_sessionmaker)).evidence_pack_json is None


@pytest.mark.usefixtures("seeded_case")
async def test_moderate_risk_gate_ignores_advisory_approve(pg_url, pg_sessionmaker):
    """Risk 30-59 without HIGH_RISK (recommendation would be shown): gate still unchanged.

    Every finding worth >= 30 points also adds a gate reason, so such a case always freezes;
    AUTO_SETTLE/SHORT_PAY cannot be reached at risk >= 30.
    """
    async with pg_sessionmaker.begin() as session:
        session.add(
            make_execution(
                "e0",
                status="FROZEN",
                policy_version=2,
                invoice_number_norm="INV1043",  # within two edits of INV1042: fuzzy duplicate
                billed_cents=48_000,
            )
        )
    approve = AsyncMock(return_value=pack("APPROVE"))
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        with_pack = await run_case(saver, pg_sessionmaker, "m-pack", "CLEAN", investigator=approve)
        await reset(pg_sessionmaker)
        without = await run_case(saver, pg_sessionmaker, "m-none", "CLEAN")
    approve.assert_awaited_once()
    assert 30 <= with_pack["risk"].score < 60
    assert "HIGH_RISK" not in with_pack["gate"].reasons
    assert with_pack["gate"] == without["gate"] and with_pack["gate"].route == "FREEZE"


async def slow(execution_id, state):
    await asyncio.sleep(5)
    return pack("APPROVE")


async def halting(execution_id, state):
    # TokenOps budget halt (BaseException): A4 is advisory, the case must still reach the gate.
    raise Halt(Action(kind=ActionKind.HALT, trace_id="t", reason="cap"))


@pytest.mark.usefixtures("seeded_case")
@pytest.mark.parametrize("investigator", [slow, halting], ids=["timeout", "halt"])
async def test_timeout_or_halt_means_unavailable_and_case_freezes(
    pg_url, pg_sessionmaker, investigator
):
    investigator = AsyncMock(side_effect=investigator)
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        values = await run_case(
            saver,
            pg_sessionmaker,
            "t-stuck",
            investigator=investigator,
            investigate_timeout_s=0.1,
        )
    investigator.assert_awaited_once()  # the timeout/halt path really ran, not a skipped node
    assert values["evidence"] is None and values["gate"].route == "FREEZE"
    assert "HIGH_RISK" in values["gate"].reasons  # the gate still ran on full facts
    assert (await stored(pg_sessionmaker)).status == "FROZEN"  # never stuck in ANALYZING


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
    async with sessions.begin() as session:
        await seed_world(session)
        await session.flush()
        session.add(
            make_execution(
                EXECUTION, status="ANALYZING", paypal_invoice_id="INV2-ABC", replayed=replayed
            )
        )
        session.add(make_execution("e0", status="SETTLED", billed_cents=48_000))


def capture_tools(monkeypatch):
    """A run that records the real ReadTools built from the real snapshot."""
    seen = {}
    original = wiring.investigate

    def spy(complete, tools, *, case):
        seen["tools"] = tools
        return original(complete, tools, case=case)

    monkeypatch.setattr(wiring, "investigate", spy)

    def run(run_id, *, settings, action):
        return action(lambda *a, **k: ModelResponse(content="{}"))

    return seen, run


async def test_live_path_uses_governed_run_and_match_invoice(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=False)
    calls = []

    def run(run_id, *, settings, action):
        calls.append(run_id)
        return action(scripted(VENDOR_CALL, VENDOR_FINAL))

    evidence = await build_investigator(pg_sessionmaker, object(), run=run)(EXECUTION, STATE)
    assert evidence and evidence.facts[0].excerpt == '"VERIFIED"'
    assert len(calls) == 1  # one budget scope, keyed by the execution (shared durable row)


async def test_real_snapshot_tags_vendor_text_and_has_no_payee_ids(pg_sessionmaker, monkeypatch):
    """M1/M2: production snapshot -> ReadTools -> wrap/tag, no payee identifiers at all."""
    await seed_execution(pg_sessionmaker, replayed=False)
    seen, run = capture_tools(monkeypatch)
    await build_investigator(pg_sessionmaker, object(), run=run)(EXECUTION, STATE)
    tools = seen["tools"]
    outputs = {name: tools.call(name, {}) for name in TOOL_NAMES}
    everything = "".join(outputs.values())
    for secret in (PAYER_ID, "supplier@x.example", "invoicer_email", "paypal_payer_id"):
        assert secret not in everything
    invoice, vendor = outputs["get_invoice"], outputs["get_vendor"]
    number = STATE["match"].invoice_number_norm
    assert number and is_vendor_asserted(invoice, number)
    assert is_vendor_asserted(vendor, "Supplier")  # vendor_name from onboarding
    assert not is_vendor_asserted(invoice, '"invoice_id": "INV2-ABC"')
    assert not is_vendor_asserted(vendor, '"trust_status": "VERIFIED"')
    assert json.loads(invoice)["total_cents"] == STATE["match"].billed_cents


async def test_prior_executions_exclude_current_case(pg_sessionmaker, monkeypatch):
    await seed_execution(pg_sessionmaker, replayed=False)
    seen, run = capture_tools(monkeypatch)
    await build_investigator(pg_sessionmaker, object(), run=run)(EXECUTION, STATE)
    out = seen["tools"].call("list_prior_executions", {})
    assert '"execution_id": "e0"' in out and '"execution_id": "e1"' not in out


async def test_replayed_without_recording_never_calls_provider(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=True)
    run = AsyncMock()
    assert await build_investigator(pg_sessionmaker, object(), run=run)(EXECUTION, STATE) is None
    run.assert_not_called()


async def test_replayed_uses_replay_completion_only(pg_sessionmaker):
    await seed_execution(pg_sessionmaker, replayed=True)
    run = AsyncMock()
    investigator = build_investigator(
        pg_sessionmaker, object(), replay_complete=scripted(VENDOR_CALL, VENDOR_FINAL), run=run
    )
    evidence = await investigator(EXECUTION, STATE)
    assert evidence and evidence.advisory_recommendation == "NEEDS_INFO"
    run.assert_not_called()


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


@pytest.mark.parametrize(
    ("status", "reasons", "dispatcher", "shown"),
    [
        ("SETTLED", ["POSSIBLE_DUPLICATE"], True, True),  # GateDecision object path
        ("SETTLED", ["HIGH_RISK"], True, False),
        ("FROZEN", ["POSSIBLE_DUPLICATE"], True, True),  # no pending review: dict gate path
        ("FROZEN", ["HIGH_RISK"], True, False),
        ("SETTLED", ["POSSIBLE_DUPLICATE"], False, False),  # gate unknown: withheld
    ],
)
async def test_case_endpoint_withholds_advisory_on_high_risk(
    pg_sessionmaker, case, status, reasons, dispatcher, shown
):
    await seed_settled(pg_sessionmaker, status=status)
    async with pg_sessionmaker.begin() as session:
        row = await session.get(InvoiceExecution, "e1")
        row.evidence_pack_json = pack("APPROVE").model_dump(mode="json")
    if dispatcher:
        snapshot = SimpleNamespace(values={"gate": gate(reasons)}, tasks=[])
        stub = SimpleNamespace(snapshot=AsyncMock(return_value=snapshot))
        app.state.dispatcher = stub  # the case fixture's monkeypatch restores it
    evidence = (await case())["evidence"]
    assert evidence["facts"] == pack().model_dump(mode="json")["facts"]
    assert evidence["advisory_recommendation"] == ("APPROVE" if shown else None)
