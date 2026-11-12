"""Checkpointed orchestration of the invoice decision, with server-bound R11 resumes."""

import logging
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select, update

from app.decision.match import match
from app.decision.policy_gate import _hash, evaluate
from app.decision.risk import assess
from app.decision.short_pay import decide
from app.forensics.classifier import UNAVAILABLE
from app.models.core import MandatePolicy as PolicyRow
from app.models.core import Operator, VendorMaster
from app.models.payables import Approval, InvoiceDocument, InvoiceExecution
from app.schemas.decision import AegisState, GateDecision, MandatePolicy

log = logging.getLogger(__name__)


class GraphState(AegisState, total=False):
    policy_id: str
    policy_version: int
    analysis_failure: str | None
    match_failure: str | None
    vendor_trust_status: str
    approval_retry: bool


class ResumeRejected(ValueError):
    """Graph refused the approval before recording acceptance."""


def _failed_gate(state: GraphState, reason: str) -> GateDecision:
    return GateDecision(
        route="FREEZE",
        payable_cents=0,
        reasons=[reason],
        required_approvals=99,
        allowed_actions=["REJECT"],
        policy_id=state["policy_id"],
        policy_version=state["policy_version"],
        gate_hash=_hash(
            [state["execution_id"], state["policy_id"], state["policy_version"], reason]
        ),
    )


def _json(value):
    return value.model_dump(mode="json") if value is not None else None


async def _pending_binding(session, state: GraphState, config: RunnableConfig, approval_id: str):
    if not isinstance(approval_id, str):
        raise ResumeRejected("expected only an approval id")
    gate = state["gate"]
    query = (
        select(Approval, Operator, InvoiceDocument, InvoiceExecution)
        .select_from(Approval)
        .join(Operator, Operator.operator_id == Approval.actor)
        .join(InvoiceExecution, InvoiceExecution.execution_id == Approval.execution_id)
        .join(InvoiceDocument, InvoiceDocument.document_id == InvoiceExecution.document_id)
        .where(Approval.approval_id == approval_id)
    )
    row = (await session.execute(query)).one_or_none()
    if row is None:
        raise ResumeRejected("approval not found")
    approval, actor, document, execution = row
    expected = {
        "execution_id": execution.execution_id,
        "checkpoint_id": approval.resume_checkpoint_id,
        "interrupt_id": approval.resume_interrupt_id,
        "gate_hash": approval.gate_hash,
    }
    configurable = config.get("configurable", {})
    if (
        configurable.get("resume_binding") != expected
        or configurable.get("thread_id") != execution.langgraph_thread_id
        or execution.execution_id != state["execution_id"]
        or execution.status != "FROZEN"
        or gate.route != "FREEZE"
        or gate.gate_hash != approval.gate_hash
        or approval.voided_at is not None
        or approval.consumed_at is not None
        or approval.action not in gate.allowed_actions
        or len(approval.reason.strip()) < 10
        or actor.disabled
        or actor.role not in ("controller", "cfo")
        or actor.operator_id == document.uploaded_by
    ):
        raise ResumeRejected("approval does not match the pending gate")
    return expected


def build_graph(
    checkpointer,
    *,
    sessions,
    analyze,
    reader,
    approve,
    transition,
    replay_reader=None,
    notify=None,
    vendor_new_days=7,
    max_vendor_invoices=5,
):
    """Compile with a Postgres saver in the API, or an in-memory saver for tests."""

    async def ingest(state: GraphState):
        execution_id = state["execution_id"]
        async with sessions.begin() as session:
            execution = await session.get(InvoiceExecution, execution_id, with_for_update=True)
            if execution is None:
                raise ValueError("unknown execution")
            if execution.status == "RECEIVED":
                await transition(session, execution, "ANALYZING", actor="system:decision")
            elif execution.status != "ANALYZING":
                raise ValueError("execution not ready for analysis")
            refs = {
                "document_id": execution.document_id,
                "policy_id": execution.policy_id,
                "policy_version": execution.policy_version,
                "po_id": execution.po_id,
                "vendor_id": execution.vendor_id,
            }
        try:
            extraction, forensics = await analyze(execution_id)
            return {
                **refs,
                "extraction": extraction.extraction,
                "forensics": forensics,
                "analysis_failure": extraction.freeze_reason,
            }
        except Exception as exc:
            log.warning("ingest failed execution_id=%s error=%s", execution_id, type(exc).__name__)
            return {**refs, "analysis_failure": "ANALYSIS_UNAVAILABLE"}

    def forensics(state: GraphState):
        report = state.get("forensics")
        if report and report.classifier_rationale == UNAVAILABLE:
            return {"analysis_failure": "CLASSIFIER_UNAVAILABLE"}
        return {}

    async def match_node(state: GraphState):
        execution_id = state["execution_id"]
        failure = state.get("analysis_failure")
        reset = {"match": None, "risk": None, "short_pay": None}
        if failure:
            return {**reset, "match_failure": failure}
        try:
            async with sessions() as session:
                execution = await session.get(InvoiceExecution, execution_id)
                source = replay_reader if execution.replayed else reader
                if source is None:
                    return {**reset, "match_failure": "SOURCE_READER_UNAVAILABLE"}
                result = await match(state, session=session, reader=source)
                policy_row = await session.get(
                    PolicyRow, (execution.policy_id, execution.policy_version)
                )
                vendor = await session.get(VendorMaster, execution.vendor_id)
                policy = MandatePolicy.model_validate(policy_row.compiled_json, strict=True)
            return {
                **result,
                "policy": policy,
                "policy_id": policy.policy_id,
                "policy_version": policy.version,
                "vendor_trust_status": vendor.trust_status,
                "match_failure": None,
            }
        except Exception as exc:
            log.warning("match failed execution_id=%s error=%s", execution_id, type(exc).__name__)
            return {**reset, "match_failure": "MATCH_UNAVAILABLE"}

    def score_risk(state: GraphState):
        if state.get("match_failure"):
            return {}
        return {
            "risk": assess(
                state["match"],
                state["forensics"],
                state["policy"],
                vendor_trust_status=state["vendor_trust_status"],
                vendor_new_days=vendor_new_days,
                max_vendor_invoices=max_vendor_invoices,
            )
        }

    def investigate(state: GraphState):
        # shortcut: A4 evidence is unavailable until the P3-01 investigator lands.
        return {"evidence": None}

    def short_pay_decide(state: GraphState):
        if state.get("match_failure"):
            return {}
        return {"short_pay": decide(state["match"], state["policy"])}

    async def persist_result(state: GraphState, gate: GateDecision, status: str | None = None):
        async with sessions.begin() as session:
            execution = await session.get(
                InvoiceExecution, state["execution_id"], with_for_update=True
            )
            if execution.status not in ("ANALYZING", "FROZEN"):
                return
            await session.execute(
                update(Approval)
                .where(
                    Approval.execution_id == execution.execution_id,
                    Approval.gate_hash != gate.gate_hash,
                    Approval.voided_at.is_(None),
                )
                .values(voided_at=datetime.now(UTC))
            )
            execution.route = gate.route if gate.route != "REJECTED" else "FREEZE"
            match_report = state.get("match")
            if match_report is not None and match_report.paypal_invoice_id is not None:
                execution.billed_cents = match_report.billed_cents
            execution.match_report_json = _json(match_report)
            risk = state.get("risk")
            execution.risk_score = risk.score if risk is not None else None
            execution.risk_json = _json(risk)
            execution.short_pay_json = _json(state.get("short_pay"))
            execution.evidence_pack_json = _json(state.get("evidence"))
            if status is not None and status != execution.status:
                await transition(session, execution, status, actor="system:decision")

    async def policy_gate(state: GraphState):
        failure = state.get("match_failure")
        if failure:
            gate = _failed_gate(state, failure)
        else:
            try:
                async with sessions() as session:
                    gate = await evaluate(
                        session,
                        execution_id=state["execution_id"],
                        policy=state["policy"],
                        match=state["match"],
                        risk=state["risk"],
                        forensics=state["forensics"],
                        vendor_new_days=vendor_new_days,
                        max_vendor_invoices=max_vendor_invoices,
                    )
            except Exception as exc:
                log.warning(
                    "gate failed execution_id=%s error=%s",
                    state["execution_id"],
                    type(exc).__name__,
                )
                gate = _failed_gate(state, "GATE_UNAVAILABLE")
        if gate.route == "FREEZE" and gate.required_approvals == 99:
            async with sessions() as session:
                rejected = (
                    await session.execute(
                        select(Approval)
                        .join(Operator, Operator.operator_id == Approval.actor)
                        .join(
                            InvoiceExecution, InvoiceExecution.execution_id == Approval.execution_id
                        )
                        .join(
                            InvoiceDocument,
                            InvoiceDocument.document_id == InvoiceExecution.document_id,
                        )
                        .where(
                            Approval.execution_id == state["execution_id"],
                            Approval.gate_hash == gate.gate_hash,
                            Approval.action == "REJECT",
                            Approval.voided_at.is_(None),
                            Operator.disabled.is_(False),
                            Operator.role.in_(("controller", "cfo")),
                            Operator.operator_id != InvoiceDocument.uploaded_by,
                        )
                    )
                ).scalars()
                for vote in rejected:
                    binding = {
                        "execution_id": vote.execution_id,
                        "checkpoint_id": vote.resume_checkpoint_id,
                        "interrupt_id": vote.resume_interrupt_id,
                        "gate_hash": vote.gate_hash,
                    }
                    if state.get("resume_receipts", {}).get(vote.approval_id) == binding:
                        gate = gate.model_copy(
                            update={
                                "route": "REJECTED",
                                "required_approvals": 0,
                                "allowed_actions": [],
                            }
                        )
                        break
        if gate.route == "FREEZE":
            await persist_result(state, gate, "FROZEN")
        return {"gate": gate}

    async def human_review(state: GraphState, config: RunnableConfig):
        gate = state["gate"]
        approval_id = interrupt(
            {
                "execution_id": state["execution_id"],
                "reasons": gate.reasons,
                "required_approvals": gate.required_approvals,
                "allowed_actions": gate.allowed_actions,
                "gate_hash": gate.gate_hash,
            }
        )
        async with sessions() as session:
            binding = await _pending_binding(session, state, config, approval_id)
            votes = await session.scalars(
                select(Approval.approval_id)
                .where(
                    Approval.execution_id == state["execution_id"],
                    Approval.gate_hash == gate.gate_hash,
                    Approval.voided_at.is_(None),
                )
                .order_by(Approval.approval_id)
            )
            return {
                "approval_ids": list(votes),
                "resume_receipts": {**state.get("resume_receipts", {}), approval_id: binding},
                "approval_retry": False,
            }

    async def approve_for_payment(state: GraphState):
        await persist_result(state, state["gate"])
        if await approve(state["execution_id"], state):
            if notify is not None and state["gate"].route == "SHORT_PAY":
                try:
                    await notify(state["execution_id"])  # §12.4, idempotent on execution_id
                except Exception:
                    # shortcut: payment is already committed, so a notice DB failure only logs;
                    # add a notice backfill sweep if this is ever seen outside tests.
                    log.exception("notice failed execution_id=%s", state["execution_id"])
            return {"approval_retry": False}
        if not state.get("approval_retry"):
            return {"approval_retry": True}
        gate = _failed_gate(state, "APPROVAL_FACTS_CHANGED")
        await persist_result(state, gate, "FROZEN")
        return {"gate": gate}

    def after_approval(state: GraphState):
        if state["gate"].route == "FREEZE":
            return "human_review"
        if state.get("approval_retry"):
            return "match"
        return END

    async def close_rejected(state: GraphState):
        await persist_result(state, state["gate"], "REJECTED")
        return {}

    async def close_duplicate(state: GraphState):
        await persist_result(state, state["gate"], "DUPLICATE_BLOCKED")
        return {}

    graph = StateGraph(GraphState)
    graph.add_node("ingest", ingest)
    graph.add_node("forensics", forensics)
    graph.add_node("match", match_node)
    graph.add_node("score_risk", score_risk)
    graph.add_node("investigate", investigate)
    graph.add_node("short_pay_decide", short_pay_decide)
    graph.add_node("policy_gate", policy_gate)
    graph.add_node("human_review", human_review)
    graph.add_node("approve_for_payment", approve_for_payment)
    graph.add_node("close_rejected", close_rejected)
    graph.add_node("close_duplicate", close_duplicate)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "forensics")
    graph.add_edge("forensics", "match")
    graph.add_edge("match", "score_risk")
    graph.add_conditional_edges(
        "score_risk",
        lambda s: bool(s.get("risk") and s["risk"].score >= 30 and "evidence" not in s),
        {True: "investigate", False: "short_pay_decide"},
    )
    graph.add_edge("investigate", "short_pay_decide")
    graph.add_edge("short_pay_decide", "policy_gate")
    graph.add_conditional_edges(
        "policy_gate",
        lambda s: s["gate"].route,
        {
            "AUTO_SETTLE": "approve_for_payment",
            "SHORT_PAY": "approve_for_payment",
            "FREEZE": "human_review",
            "DUPLICATE_BLOCKED": "close_duplicate",
            "REJECTED": "close_rejected",
        },
    )
    graph.add_edge("human_review", "match")
    graph.add_conditional_edges(
        "approve_for_payment", after_approval, ["human_review", "match", END]
    )
    graph.add_edge("close_rejected", END)
    graph.add_edge("close_duplicate", END)
    return graph.compile(checkpointer=checkpointer)
