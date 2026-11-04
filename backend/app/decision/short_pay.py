"""PRD §12: compare remedy costs in integer cents; never determine a payout amount."""

from decimal import ROUND_HALF_UP, Decimal

from app.schemas.decision import MandatePolicy, MatchReport, ShortPayDecision


def decide(match: MatchReport, policy: MandatePolicy) -> ShortPayDecision:
    match = MatchReport.model_validate(match.model_dump(), strict=True)
    policy = MandatePolicy.model_validate(policy.model_dump(), strict=True)
    assumptions = policy.short_pay_assumptions
    delta = max(match.billed_cents - match.accepted_value_cents, 0)
    claim = (
        (Decimal(1) - Decimal(str(assumptions.p_recover))) * delta
        + assumptions.claim_ops_cost_cents
        + Decimal(delta)
        * Decimal(str(assumptions.annual_cost_of_capital))
        * assumptions.claim_days
        / Decimal(365)
    )
    notice = (
        assumptions.notice_ops_cost_cents
        + Decimal(str(assumptions.p_vendor_contests)) * assumptions.contest_cost_cents
    )
    return ShortPayDecision(
        delta_cents=delta,
        expected_cost_pay_and_claim_cents=int(claim.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        expected_cost_short_pay_cents=int(notice.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        chosen=(
            "NOT_APPLICABLE"
            if delta == 0
            else "SHORT_PAY"
            if policy.auto_short_pay_enabled
            and delta <= policy.max_short_pay_delta_cents
            and notice < claim
            else "PAY_AND_CLAIM"
        ),
        assumptions=assumptions,
    )
