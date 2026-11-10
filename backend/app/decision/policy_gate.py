"""G1–G14 and R11: deterministic routing over fresh trusted facts and eligible votes."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.decision.match import _duplicates, _fresh
from app.decision.risk import assess, price_variance
from app.decision.short_pay import decide
from app.models.core import (
    GoodsReceipt,
    Operator,
    PurchaseOrder,
    VendorMaster,
    VendorMasterChange,
)
from app.models.core import (
    MandatePolicy as PolicyRow,
)
from app.models.payables import (
    Approval,
    InvoiceDocument,
    InvoiceExecution,
    POLedger,
    SettlementControl,
    SpendLedger,
)
from app.schemas.decision import ForensicsReport, GateDecision, MandatePolicy, MatchReport


class GateUnavailable(ValueError):
    """Caller must freeze on any failed/ambiguous authoritative read."""


def _hash(data):
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode()
    ).hexdigest()


def _row(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _decision(
    *,
    policy,
    match,
    risk,
    forensics,
    committed=0,
    halted=False,
    trust_status="VERIFIED",
    cooling=False,
    hard_reasons=(),
    gate_hash="",
    votes=(),
    vendor_new_days=7,
    max_vendor_invoices=5,
):
    """All conditions contribute; hard blocks cannot be overridden by votes."""
    payable = min(match.billed_cents, match.accepted_value_cents, match.po_remaining_cents)
    checks = [
        (bool(match.duplicate_of), "DUPLICATE_BLOCKED", 99),
        (committed + payable > policy.daily_cap_cents, "OVER_DAILY_CAP", 99),
        (halted, "HALTED", 99),
        (
            min(match.billed_cents, match.accepted_value_cents) > match.po_remaining_cents,
            "PO_EXHAUSTED",
            99,
        ),
        (
            forensics.classifier_label != "CLEAN"
            or bool(forensics.hidden_segments)
            or not match.payee_matches_vendor_master
            or trust_status != "VERIFIED",
            "HIGH_RISK",
            2,
        ),
        (cooling or trust_status == "COOLING", "VENDOR_COOLING", 99),
        (trust_status == "BLOCKED", "VENDOR_BLOCKED", 99),
        (match.billed_cents > policy.max_single_invoice_cents, "OVER_SINGLE_LIMIT", 1),
        (
            price_variance(match, policy.max_price_variance_bps)
            or match.billed_qty > match.ordered_qty,
            "MISMATCH",
            1,
        ),
        (
            not match.paypal_invoice_id
            or match.paypal_invoice_status not in ("SENT", "UNPAID", "PARTIALLY_PAID"),
            "UNVERIFIED_SOURCE",
            1,
        ),
        (not match.paypal_invoice_matches_pdf, "SOURCE_MISMATCH", 1),
        (bool(match.fuzzy_duplicate_of), "POSSIBLE_DUPLICATE", 1),
        (
            match.vendor_age_days < vendor_new_days
            or match.vendor_committed_today_cents + payable > policy.vendor_daily_cap_cents
            or match.vendor_invoices_today > max_vendor_invoices,
            "VELOCITY",
            1,
        ),
        (not match.invoice_number_norm, "NO_INVOICE_NUMBER", 1),
        (not match.precision_ok, "PRECISION", 1),
        (payable <= 0, "NO_PAYABLE", 99),
    ]
    reasons = [reason for applies, reason, _ in checks if applies] + list(hard_reasons)
    required = max([level for applies, _, level in checks if applies] + [99 if hard_reasons else 0])
    route = "FREEZE"
    if not reasons:
        if (
            match.accepted_qty == match.billed_qty
            and match.billed_cents == match.accepted_value_cents
        ):
            route = "AUTO_SETTLE"
        elif match.accepted_qty < match.billed_qty and decide(match, policy).chosen == "SHORT_PAY":
            route = "SHORT_PAY"
        else:
            reasons, required = ["NEEDS_REVIEW"], 1
    if reasons and required != 99 and risk.score >= policy.dual_approval_risk_threshold:
        required = 2
    allowed = (
        ["APPROVE", "SHORT_PAY", "REJECT"]
        if required in (1, 2)
        else ["REJECT"]
        if required == 99
        else []
    )
    actions = {actor: action for actor, action in votes}
    if match.duplicate_of:
        route, allowed = "DUPLICATE_BLOCKED", []
    elif "REJECT" in actions.values():
        route, allowed = "REJECTED", []
    elif required in (1, 2) and len(actions) >= required:
        route = "SHORT_PAY" if "SHORT_PAY" in actions.values() else "AUTO_SETTLE"
        allowed = []
    return GateDecision(
        route=route,
        payable_cents=payable,
        reasons=reasons,
        required_approvals=required,
        allowed_actions=allowed,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        gate_hash=gate_hash,
    )


async def evaluate(
    session: AsyncSession,
    *,
    execution_id,
    policy,
    match,
    risk,
    forensics,
    now=None,
    vendor_new_days=7,
    max_vendor_invoices=5,
) -> GateDecision:
    """Read-only. The approval/reservation writer must lock and recheck before committing money."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise GateUnavailable("GATE_REQUIRES_AWARE_TIME")
    if session.new or session.dirty or session.deleted:
        raise GateUnavailable("GATE_REQUIRES_CLEAN_SESSION")
    connection = await session.connection()
    if await connection.get_isolation_level() != "READ COMMITTED":
        raise GateUnavailable("GATE_REQUIRES_READ_COMMITTED")
    policy = MandatePolicy.model_validate(policy.model_dump(), strict=True)
    match = MatchReport.model_validate(match.model_dump(), strict=True)
    forensics = ForensicsReport.model_validate(forensics.model_dump(), strict=True)
    execution = await _fresh(
        session, InvoiceExecution, InvoiceExecution.execution_id == execution_id
    )
    if execution is None:
        raise GateUnavailable("NO_EXECUTION")
    document = await _fresh(
        session, InvoiceDocument, InvoiceDocument.document_id == execution.document_id
    )
    vendor = await _fresh(session, VendorMaster, VendorMaster.vendor_id == execution.vendor_id)
    po = await _fresh(session, PurchaseOrder, PurchaseOrder.po_id == execution.po_id)
    mandate = await _fresh(
        session,
        PolicyRow,
        (PolicyRow.policy_id == execution.policy_id)
        & (PolicyRow.version == execution.policy_version),
    )
    control = await _fresh(session, SettlementControl, SettlementControl.id.is_(True))
    if any(row is None for row in (document, vendor, po, mandate)):
        raise GateUnavailable("NO_TRUSTED_FACTS")
    hard = []
    if not mandate.is_active or mandate.compiled_json != policy.model_dump():
        hard.append("STALE_POLICY")
    if policy.policy_id != execution.policy_id or policy.version != execution.policy_version:
        hard.append("POLICY_BINDING_CHANGED")
    correction = aliased(GoodsReceipt)
    receipts = list(
        (
            await session.scalars(
                select(GoodsReceipt)
                .where(
                    GoodsReceipt.po_id == po.po_id,
                    ~select(correction.grn_id)
                    .where(correction.supersedes_grn_id == GoodsReceipt.grn_id)
                    .exists(),
                )
                .order_by(GoodsReceipt.created_at, GoodsReceipt.grn_id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    accepted = min(sum(row.accepted_qty for row in receipts), po.ordered_qty)
    signed_po = case(
        (POLedger.kind == "RESERVE", POLedger.amount_cents), else_=-POLedger.amount_cents
    )
    consumed = int(
        await session.scalar(
            select(func.coalesce(func.sum(signed_po), 0)).where(
                POLedger.po_id == po.po_id,
                POLedger.execution_id != execution_id,
            )
        )
    )
    remaining = max(0, accepted * po.unit_price_cents - consumed) if po.status == "OPEN" else 0
    expected = (
        po.po_id,
        po.version,
        vendor.version,
        po.ordered_qty,
        po.unit_price_cents,
        accepted,
        accepted * po.unit_price_cents,
        remaining,
    )
    reported = (
        match.po_id,
        match.po_version,
        match.vendor_version,
        match.ordered_qty,
        match.po_unit_price_cents,
        match.accepted_qty,
        match.accepted_value_cents,
        match.po_remaining_cents,
    )
    if (
        not receipts
        or expected != reported
        or match.grn_id != receipts[-1].grn_id
        or po.vendor_id != vendor.vendor_id
        or consumed < 0
    ):
        hard.append("STALE_TRUSTED_FACTS")
    if match.paypal_invoice_id != execution.paypal_invoice_id:
        hard.append("SOURCE_BINDING_CHANGED")
    zone = ZoneInfo(policy.cap_timezone)
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    signed = case(
        (SpendLedger.kind == "RESERVE", SpendLedger.amount_cents), else_=-SpendLedger.amount_cents
    )
    spend_filter = (
        SpendLedger.policy_id == policy.policy_id,
        SpendLedger.business_date == day,
        SpendLedger.execution_id != execution_id,
    )
    committed = int(
        await session.scalar(select(func.coalesce(func.sum(signed), 0)).where(*spend_filter))
    )
    vendor_committed = int(
        await session.scalar(
            select(func.coalesce(func.sum(signed), 0)).where(
                *spend_filter, SpendLedger.vendor_id == vendor.vendor_id
            )
        )
    )
    count = int(
        await session.scalar(
            select(func.count())
            .select_from(InvoiceExecution)
            .where(
                InvoiceExecution.vendor_id == vendor.vendor_id,
                InvoiceExecution.policy_id == policy.policy_id,
                InvoiceExecution.status.not_in(("REJECTED", "DUPLICATE_BLOCKED")),
                InvoiceExecution.replayed == execution.replayed,
                InvoiceExecution.created_at >= start,
                InvoiceExecution.created_at < end,
            )
        )
    )
    match.vendor_age_days = max(0, (now - vendor.onboarded_at).days)
    match.vendor_committed_today_cents = vendor_committed
    match.vendor_invoices_today = count
    await _duplicates(session, execution, match, now)
    cooling = list(
        (
            await session.scalars(
                select(VendorMasterChange)
                .where(
                    VendorMasterChange.vendor_id == vendor.vendor_id,
                    VendorMasterChange.status == "COOLING",
                )
                .order_by(VendorMasterChange.change_id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    risk = assess(
        match,
        forensics,
        policy,
        vendor_trust_status=vendor.trust_status,
        vendor_new_days=vendor_new_days,
        max_vendor_invoices=max_vendor_invoices,
    )
    gate_hash = _hash(
        {
            "policy": _row(mandate),
            "match": match.model_dump(),
            "forensics": forensics.model_dump(),
            "vendor": _row(vendor),
            "po": _row(po),
            "receipts": [_row(row) for row in receipts],
            "cooling": [_row(row) for row in cooling],
            "day": day,
            "committed": committed,
            "halted": control is None or control.halted,
            "execution_id": execution_id,
            "document": {
                "id": document.document_id,
                "sha256": document.sha256,
                "uploaded_by": document.uploaded_by,
            },
            "vendor_new_days": vendor_new_days,
            "max_vendor_invoices": max_vendor_invoices,
        }
    )
    votes = list(
        (
            await session.execute(
                select(Approval.actor, Approval.action)
                .join(
                    Operator,
                    Operator.operator_id == Approval.actor,
                )
                .where(
                    Approval.execution_id == execution_id,
                    Approval.gate_hash == gate_hash,
                    Approval.voided_at.is_(None),
                    Approval.actor != document.uploaded_by,
                    Operator.role.in_(("controller", "cfo")),
                    Operator.disabled.is_(False),
                )
            )
        ).all()
    )
    return _decision(
        policy=policy,
        match=match,
        risk=risk,
        forensics=forensics,
        committed=committed,
        halted=control is None or control.halted,
        trust_status=vendor.trust_status,
        cooling=bool(cooling),
        hard_reasons=hard,
        gate_hash=gate_hash,
        votes=votes,
        vendor_new_days=vendor_new_days,
        max_vendor_invoices=max_vendor_invoices,
    )
