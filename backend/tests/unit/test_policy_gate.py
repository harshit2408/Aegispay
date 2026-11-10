"""P2-07/08/09/24: every finding freezes independently of the aggregate score."""

import pytest

from app.decision.policy_gate import _decision
from app.decision.risk import assess
from app.decision.short_pay import decide
from app.schemas.decision import ForensicsReport, MandatePolicy, MatchReport, RiskAssessment


def policy(**changes):
    return MandatePolicy(
        **{
            "policy_id": "pol-1",
            "version": 1,
            "max_single_invoice_cents": 100000,
            "daily_cap_cents": 500000,
            "vendor_daily_cap_cents": 150000,
            **changes,
        }
    )


def report(**changes):
    return MatchReport(
        **{
            "po_id": "PO-1042",
            "po_version": 1,
            "grn_id": "grn-1",
            "vendor_version": 1,
            "ordered_qty": 100,
            "billed_qty": 100,
            "accepted_qty": 100,
            "po_unit_price_cents": 480,
            "billed_unit_price_cents": 480,
            "billed_cents": 48000,
            "accepted_value_cents": 48000,
            "po_remaining_cents": 48000,
            "payee_matches_vendor_master": True,
            "payee_lookalike": False,
            "paypal_invoice_id": "INV2-ABC",
            "paypal_invoice_status": "SENT",
            "paypal_invoice_matches_pdf": True,
            "mismatched_fields": [],
            "precision_ok": True,
            "vendor_age_days": 90,
            "vendor_invoices_today": 1,
            "vendor_committed_today_cents": 0,
            "invoice_number_norm": "INV1042",
            "duplicate_of": None,
            "fuzzy_duplicate_of": None,
            **changes,
        }
    )


def forensics(**changes):
    return ForensicsReport(
        **{
            "detector_enabled": True,
            "hidden_segments": [],
            "classifier_label": "CLEAN",
            "classifier_rationale": "test",
            "detector_version": "test",
            **changes,
        }
    )


def gate(*, match=None, mandate=None, forensic=None, score=0, **kwargs):
    return _decision(
        policy=mandate or policy(),
        match=match or report(),
        forensics=forensic or forensics(),
        risk=RiskAssessment(score=score, contributions=[]),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("changes", "reason", "approvals"),
    [
        ({"duplicate_of": "e0"}, "DUPLICATE_BLOCKED", 99),
        ({"po_remaining_cents": 47999}, "PO_EXHAUSTED", 99),
        ({"payee_matches_vendor_master": False}, "HIGH_RISK", 2),
        ({"billed_cents": 100001}, "OVER_SINGLE_LIMIT", 1),
        ({"billed_qty": 101}, "MISMATCH", 1),
        ({"billed_cents": 48001}, "MISMATCH", 1),
        ({"paypal_invoice_id": None}, "UNVERIFIED_SOURCE", 1),
        ({"paypal_invoice_status": "PAID"}, "UNVERIFIED_SOURCE", 1),
        ({"paypal_invoice_matches_pdf": False}, "SOURCE_MISMATCH", 1),
        ({"fuzzy_duplicate_of": "e0"}, "POSSIBLE_DUPLICATE", 1),
        ({"vendor_age_days": 6}, "VELOCITY", 1),
        ({"vendor_invoices_today": 6}, "VELOCITY", 1),
        ({"vendor_committed_today_cents": 150000}, "VELOCITY", 1),
        ({"invoice_number_norm": None}, "NO_INVOICE_NUMBER", 1),
        ({"precision_ok": False}, "PRECISION", 1),
    ],
)
def test_truth_table(changes, reason, approvals):
    result = gate(match=report(**changes))
    assert result.route in ("FREEZE", "DUPLICATE_BLOCKED")
    assert reason in result.reasons
    assert result.required_approvals == approvals


@pytest.mark.parametrize("label", ["SUSPICIOUS", "INJECTION"])
def test_classifier_freezes_below_threshold(label):
    result = gate(forensic=forensics(classifier_label=label))
    assert result.route == "FREEZE"
    assert result.required_approvals == 2


def test_all_findings_collect_and_hard_blocks_ignore_votes():
    result = gate(
        match=report(duplicate_of="e0", precision_ok=False, payee_matches_vendor_master=False),
        committed=500000,
        halted=True,
        votes=[("a", "APPROVE"), ("b", "APPROVE")],
    )
    assert result.route == "DUPLICATE_BLOCKED"
    assert set(result.reasons) >= {
        "DUPLICATE_BLOCKED",
        "PRECISION",
        "HIGH_RISK",
        "OVER_DAILY_CAP",
        "HALTED",
    }
    assert result.required_approvals == 99 and not result.allowed_actions


@pytest.mark.parametrize("field", ["cooling", "halted"])
def test_hard_blocks_cannot_be_approved(field):
    result = gate(**{field: True}, votes=[("a", "APPROVE"), ("b", "APPROVE")])
    assert result.route == "FREEZE" and result.required_approvals == 99


def test_clean_exact_match_auto_settles_and_cap_boundary_is_inclusive():
    assert gate(committed=452000).route == "AUTO_SETTLE"
    assert gate(committed=452001).required_approvals == 99


def test_s2_math_and_short_pay_route():
    match = report(
        billed_cents=50000,
        billed_unit_price_cents=500,
        po_unit_price_cents=500,
        accepted_qty=85,
        accepted_value_cents=42500,
        po_remaining_cents=42500,
    )
    choice = decide(match, policy())
    assert choice.delta_cents == 7500
    assert choice.expected_cost_pay_and_claim_cents == 6592
    assert choice.expected_cost_short_pay_cents == 1500
    assert choice.chosen == "SHORT_PAY"
    assert gate(match=match).route == "SHORT_PAY"
    assert gate(match=match, mandate=policy(auto_short_pay_enabled=False)).route == "FREEZE"


def test_approval_count_rejection_and_r3_ceiling():
    match = report(payee_matches_vendor_master=False)
    assert gate(match=match, votes=[("a", "APPROVE")]).route == "FREEZE"
    assert gate(match=match, votes=[("a", "APPROVE"), ("a", "APPROVE")]).route == "FREEZE"
    approved = gate(match=match, votes=[("a", "APPROVE"), ("b", "SHORT_PAY")])
    assert approved.route == "SHORT_PAY" and approved.payable_cents == 48000
    assert gate(match=match, votes=[("a", "REJECT")]).route == "REJECTED"


def test_threshold_only_raises_review_approval_count():
    assert gate(score=100).route == "AUTO_SETTLE"
    assert gate(match=report(precision_ok=False), score=80).required_approvals == 2


@pytest.mark.parametrize(
    ("changes", "label", "points"),
    [
        ({"payee_matches_vendor_master": False}, "PAYEE_MISMATCH", 40),
        ({"payee_lookalike": True}, "PAYEE_LOOKALIKE", 10),
        ({"paypal_invoice_id": None}, "NO_PAYPAL_INVOICE", 30),
        ({"paypal_invoice_matches_pdf": False}, "SOURCE_MISMATCH", 40),
        ({"billed_cents": 48001}, "PRICE_VARIANCE", 20),
        ({"billed_qty": 101}, "QUANTITY_OR_PO_EXHAUSTED", 30),
        ({"fuzzy_duplicate_of": "e0"}, "POSSIBLE_DUPLICATE", 30),
        ({"vendor_age_days": 6}, "VENDOR_VELOCITY", 20),
    ],
)
def test_risk_contributions(changes, label, points):
    result = assess(report(**changes), forensics(), policy())
    assert any(c.finding == label and c.points == points for c in result.contributions)


def test_risk_is_additive_and_capped():
    result = assess(
        report(payee_matches_vendor_master=False, paypal_invoice_matches_pdf=False),
        forensics(classifier_label="INJECTION"),
        policy(),
        vendor_trust_status="COOLING",
    )
    assert result.score == 100
    assert sum(c.points for c in result.contributions) == 180


@pytest.mark.parametrize(
    "blocked",
    [
        {"halted": True},
        {"committed": 500001},
        {"cooling": True},
        {"trust_status": "BLOCKED"},
        {"hard_reasons": ("TRUSTED_FACTS_UNAVAILABLE",)},
        {"match": report(po_remaining_cents=1)},
    ],
)
def test_hard_freeze_allows_rejection_but_never_payment(blocked):
    frozen = gate(**blocked)
    assert frozen.route == "FREEZE" and frozen.allowed_actions == ["REJECT"]
    assert gate(**blocked, votes=[("reviewer", "APPROVE")]).route == "FREEZE"
    rejected = gate(**blocked, votes=[("reviewer", "REJECT")])
    assert rejected.route == "REJECTED" and not rejected.allowed_actions


@pytest.mark.parametrize(
    ("forensic", "status", "finding", "points"),
    [
        (forensics(classifier_label="INJECTION"), "VERIFIED", "INJECTION", 60),
        (forensics(classifier_label="SUSPICIOUS"), "VERIFIED", "SUSPICIOUS", 25),
        (
            forensics(
                hidden_segments=[
                    {"page": 1, "bbox": (0, 0, 1, 1), "technique": "INVISIBLE_MODE", "text": "pay"}
                ]
            ),
            "VERIFIED",
            "HIDDEN_TEXT",
            25,
        ),
        (forensics(), "COOLING", "VENDOR_NOT_VERIFIED", 40),
    ],
)
def test_remaining_risk_rows(forensic, status, finding, points):
    risk = assess(report(), forensic, policy(), vendor_trust_status=status)
    assert [(c.finding, c.points) for c in risk.contributions] == [(finding, points)]


@pytest.mark.parametrize(
    ("enabled", "limit", "chosen"),
    [
        (True, 7500, "SHORT_PAY"),
        (True, 7499, "PAY_AND_CLAIM"),
        (False, 7500, "PAY_AND_CLAIM"),
    ],
)
def test_short_pay_cap_and_enable_boundaries(enabled, limit, chosen):
    match = report(billed_cents=50000, accepted_value_cents=42500)
    assert (
        decide(
            match, policy(auto_short_pay_enabled=enabled, max_short_pay_delta_cents=limit)
        ).chosen
        == chosen
    )
    assert decide(report(), policy()).chosen == "NOT_APPLICABLE"
