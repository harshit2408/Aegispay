"""PRD §10.2 additive risk; findings also have independent policy-gate checks."""

from app.schemas.decision import (
    ForensicsReport,
    MandatePolicy,
    MatchReport,
    RiskAssessment,
    RiskContribution,
)


def price_variance(match: MatchReport, tolerance_bps: int) -> bool:
    expected = match.billed_qty * match.po_unit_price_cents
    return abs(match.billed_cents - expected) * 10_000 > expected * tolerance_bps


def assess(
    match: MatchReport,
    forensics: ForensicsReport,
    policy: MandatePolicy,
    *,
    vendor_trust_status: str = "VERIFIED",
    vendor_new_days: int = 7,
    max_vendor_invoices: int = 5,
) -> RiskAssessment:
    payable = min(match.billed_cents, match.accepted_value_cents, match.po_remaining_cents)
    checks = [
        (forensics.classifier_label == "INJECTION", "INJECTION", 60),
        (forensics.classifier_label == "SUSPICIOUS", "SUSPICIOUS", 25),
        (bool(forensics.hidden_segments), "HIDDEN_TEXT", 25),
        (not match.payee_matches_vendor_master, "PAYEE_MISMATCH", 40),
        (match.payee_lookalike, "PAYEE_LOOKALIKE", 10),
        (not match.paypal_invoice_id, "NO_PAYPAL_INVOICE", 30),
        (not match.paypal_invoice_matches_pdf, "SOURCE_MISMATCH", 40),
        (price_variance(match, policy.max_price_variance_bps), "PRICE_VARIANCE", 20),
        (
            match.billed_qty > match.ordered_qty
            or min(match.billed_cents, match.accepted_value_cents) > match.po_remaining_cents,
            "QUANTITY_OR_PO_EXHAUSTED",
            30,
        ),
        (bool(match.fuzzy_duplicate_of), "POSSIBLE_DUPLICATE", 30),
        (vendor_trust_status != "VERIFIED", "VENDOR_NOT_VERIFIED", 40),
        (
            match.vendor_age_days < vendor_new_days
            or match.vendor_committed_today_cents + payable > policy.vendor_daily_cap_cents
            or match.vendor_invoices_today > max_vendor_invoices,
            "VENDOR_VELOCITY",
            20,
        ),
    ]
    contributions = [
        RiskContribution(finding=finding, points=points)
        for applies, finding, points in checks
        if applies
    ]
    return RiskAssessment(
        score=min(100, sum(item.points for item in contributions)), contributions=contributions
    )
