"""Case read envelope and session-bound decision request for PRD §13 / §14.3."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.decision import (
    Cents,
    EvidencePack,
    ForensicsReport,
    GateDecision,
    InvoiceExtraction,
    MandatePolicy,
    MatchReport,
    RiskAssessment,
    ShortPayDecision,
)

Action = Literal["APPROVE", "SHORT_PAY", "REJECT"]
Role = Literal["controller", "cfo", "vendor_admin", "receiver", "admin"]
ExecutionStatus = Literal[
    "RECEIVED",
    "ANALYZING",
    "FROZEN",
    "REJECTED",
    "DUPLICATE_BLOCKED",
    "APPROVED_FOR_PAYMENT",
    "PAYOUT_SENDING",
    "PAYOUT_SUBMITTED",
    "PAYOUT_SUCCEEDED",
    "PAYOUT_FAILED",
    "SETTLED",
    "NEEDS_MANUAL_REVIEW",
]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Action
    reason: str = Field(min_length=10, max_length=4000)
    gate_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class OperatorView(BaseModel):
    operator_id: str
    role: Role


class ApprovalView(BaseModel):
    approval_id: str
    actor: OperatorView
    action: Action
    reason: str
    gate_hash: str
    void_reason: str | None


class VendorView(BaseModel):
    vendor_id: str
    name: str
    version: int
    paypal_payer_id: str
    onboarded_at: str
    change_requested_by: str | None
    change_approved_by: str | None
    change_effective_at: str | None


class PurchaseOrderView(BaseModel):
    po_id: str
    version: int
    description: str
    ordered_qty: int
    unit_price_cents: Cents


class GoodsReceiptView(BaseModel):
    grn_id: str
    version: int
    recorded_by: str
    accepted_qty: int


class PayPalInvoiceView(BaseModel):
    invoice_id: str
    status: str
    billed_qty: int
    unit_price_cents: Cents
    total_cents: Cents
    payee_reference: str


class POConsumptionView(BaseModel):
    accepted_value_cents: Cents
    paid_cents: Cents
    reserved_cents: Cents
    this_payable_cents: Cents


class SettlementEventView(BaseModel):
    status: ExecutionStatus
    occurred_at: str | None
    paypal_id: str | None
    verified: bool
    simulated: bool


class AuditAnchorView(BaseModel):
    verified: bool
    anchored_at: str | None
    head_hmac: str | None


class CaseDetail(BaseModel):
    execution_id: str
    status: ExecutionStatus
    replayed: bool
    uploaded_by: str
    viewer: OperatorView
    vendor: VendorView
    purchase_order: PurchaseOrderView
    goods_receipt: GoodsReceiptView
    paypal_invoice: PayPalInvoiceView
    po_consumption: POConsumptionView
    policy: MandatePolicy
    extraction: InvoiceExtraction
    forensics: ForensicsReport
    match: MatchReport
    risk: RiskAssessment
    evidence: EvidencePack | None
    short_pay: ShortPayDecision | None
    gate: GateDecision
    approvals: list[ApprovalView]
    settlement_timeline: list[SettlementEventView]
    audit_anchor: AuditAnchorView


class ExecutionRow(BaseModel):
    execution_id: str
    status: ExecutionStatus
    vendor_name: str
    billed_cents: Cents
    payable_cents: Cents
    risk_score: Annotated[int, Field(ge=0, le=100)]
    replayed: bool


class DecisionReceipt(BaseModel):
    approval_id: str


class APIError(BaseModel):
    detail: str
