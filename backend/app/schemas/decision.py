"""PRD §11.1 decision schemas. Cents reject coercion; ratios/bboxes follow the spec."""

from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Cents = Annotated[int, Field(ge=0, strict=True)]


class ShortPayAssumptions(BaseModel):
    p_recover: float = Field(0.60, ge=0, le=1)
    claim_ops_cost_cents: Cents = 3500
    annual_cost_of_capital: float = Field(0.10, ge=0, le=1)
    claim_days: int = Field(45, ge=0)
    notice_ops_cost_cents: Cents = 500
    p_vendor_contests: float = Field(0.10, ge=0, le=1)
    contest_cost_cents: Cents = 10000


class MandatePolicy(BaseModel):
    policy_id: str
    version: int
    max_single_invoice_cents: Cents
    daily_cap_cents: Cents
    vendor_daily_cap_cents: Cents
    cap_timezone: str = "UTC"
    max_price_variance_bps: int = Field(0, ge=0)
    auto_short_pay_enabled: bool = True
    max_short_pay_delta_cents: Cents = 10000
    dual_approval_risk_threshold: int = Field(80, ge=0)
    short_pay_assumptions: ShortPayAssumptions = ShortPayAssumptions()


class InvoiceExtraction(BaseModel):
    vendor_invoice_number: str | None
    po_reference: str | None
    payee_email: str | None
    billed_qty: int = Field(ge=0)
    unit_price_cents: Cents
    total_cents: Cents
    currency: Literal["USD"]
    field_confidence: dict[str, float]


class HiddenSegment(BaseModel):
    page: int
    bbox: tuple[float, float, float, float]
    technique: Literal[
        "COLOR_MATCH",
        "TINY_FONT",
        "OFF_PAGE",
        "INVISIBLE_MODE",
        "COVERED",
        "ANNOTATION",
        "METADATA",
        "OCR_DIFF",
    ]
    text: str


class ForensicsReport(BaseModel):
    detector_enabled: bool
    hidden_segments: list[HiddenSegment]
    classifier_label: Literal["CLEAN", "SUSPICIOUS", "INJECTION"]
    classifier_rationale: str
    detector_version: str


class MatchReport(BaseModel):
    po_id: str
    po_version: int
    grn_id: str
    vendor_version: int
    ordered_qty: int
    billed_qty: int
    accepted_qty: int
    po_unit_price_cents: Cents
    billed_unit_price_cents: Cents
    billed_cents: Cents
    accepted_value_cents: Cents
    po_remaining_cents: Cents
    payee_matches_vendor_master: bool
    payee_lookalike: bool
    paypal_invoice_id: str | None
    paypal_invoice_status: str | None
    paypal_invoice_matches_pdf: bool
    mismatched_fields: list[str]
    precision_ok: bool
    vendor_age_days: int
    vendor_invoices_today: int
    vendor_committed_today_cents: Cents
    invoice_number_norm: str | None
    duplicate_of: str | None
    fuzzy_duplicate_of: str | None


class RiskContribution(BaseModel):
    finding: str
    points: int


class RiskAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    contributions: list[RiskContribution]


class EvidenceFact(BaseModel):
    claim: str
    tool_call_id: str
    excerpt: str
    vendor_asserted: bool


class EvidencePack(BaseModel):
    summary: str
    facts: list[EvidenceFact]
    advisory_recommendation: Literal["APPROVE", "SHORT_PAY", "REJECT", "NEEDS_INFO"]


class ShortPayDecision(BaseModel):
    delta_cents: Cents
    expected_cost_pay_and_claim_cents: Cents
    expected_cost_short_pay_cents: Cents
    chosen: Literal["SHORT_PAY", "PAY_AND_CLAIM", "NOT_APPLICABLE"]
    assumptions: ShortPayAssumptions


class HumanDecision(BaseModel):
    approval_id: str
    actor: str
    action: Literal["APPROVE", "SHORT_PAY", "REJECT"]
    reason: str = Field(min_length=10)
    gate_hash: str


class GateDecision(BaseModel):
    route: Literal["AUTO_SETTLE", "SHORT_PAY", "FREEZE", "DUPLICATE_BLOCKED", "REJECTED"]
    payable_cents: Cents
    reasons: list[str]
    required_approvals: Literal[0, 1, 2, 99]
    allowed_actions: list[Literal["APPROVE", "SHORT_PAY", "REJECT"]]
    policy_id: str
    policy_version: int
    gate_hash: str


class AegisState(TypedDict, total=False):
    execution_id: str
    document_id: str
    po_id: str
    vendor_id: str
    policy: MandatePolicy
    extraction: InvoiceExtraction
    forensics: ForensicsReport
    match: MatchReport
    risk: RiskAssessment
    evidence: EvidencePack | None
    short_pay: ShortPayDecision | None
    gate: GateDecision
    approval_ids: list[str]
    resume_receipts: dict[str, dict[str, str]]
