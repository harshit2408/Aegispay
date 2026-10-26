"""Documents, executions, approvals, ledgers, outbox, payouts, audit and webhooks (PRD §8).

Write-once/immutability triggers and DB role grants land with P1-19, P1-20 and P1-22.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.core import NOW, TS

EXECUTION_STATUSES = (
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
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({','.join(repr(v) for v in values)})"


class InvoiceDocument(Base):
    __tablename__ = "invoice_documents"
    __table_args__ = (
        CheckConstraint(_in("source", ("PDF_UPLOAD", "SEEDED_INBOX")), name="ck_doc_source"),
    )

    document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    extraction_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # evidence only
    forensics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))  # R11
    uploaded_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class InvoiceExecution(Base):
    __tablename__ = "invoice_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "policy_version"],
            ["mandate_policies.policy_id", "mandate_policies.version"],
        ),
        CheckConstraint(
            _in("route", ("AUTO_SETTLE", "SHORT_PAY", "FREEZE", "DUPLICATE_BLOCKED")),
            name="ck_exec_route",
        ),
        CheckConstraint(_in("status", EXECUTION_STATUSES), name="ck_exec_status"),
        CheckConstraint("risk_score BETWEEN 0 AND 100", name="ck_exec_risk"),
        CheckConstraint(
            _in("short_pay_balance", ("NONE", "NOTICE_SENT", "ACCEPTED", "CONTESTED")),
            name="ck_exec_short_pay_balance",
        ),
        # R9: never two live executions for the same (normalized) vendor invoice
        Index(
            "uq_live_vendor_invoice",
            "vendor_id",
            "invoice_number_norm",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('REJECTED','DUPLICATE_BLOCKED') AND invoice_number_norm IS NOT NULL"
            ),
        ),
        Index(
            "uq_live_paypal_invoice",
            "paypal_invoice_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('REJECTED','DUPLICATE_BLOCKED') AND paypal_invoice_id IS NOT NULL"
            ),
        ),
    )

    execution_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("invoice_documents.document_id"))
    po_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.po_id"))
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendor_master.vendor_id"))
    vendor_invoice_number: Mapped[str | None] = mapped_column(Text)  # as asserted
    invoice_number_norm: Mapped[str | None] = mapped_column(Text)  # R9 dedupe key
    paypal_invoice_id: Mapped[str | None] = mapped_column(Text)
    langgraph_thread_id: Mapped[str] = mapped_column(Text, unique=True)
    policy_id: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[int] = mapped_column(Integer)
    billed_cents: Mapped[int | None] = mapped_column(BigInteger)  # R3, from the PayPal invoice
    approved_cents: Mapped[int | None] = mapped_column(BigInteger)  # write-once (trigger: P1-22)
    vendor_version: Mapped[int | None] = mapped_column(Integer)  # R20
    po_version: Mapped[int | None] = mapped_column(Integer)
    grn_id: Mapped[str | None] = mapped_column(ForeignKey("goods_receipts.grn_id"))
    detector_enabled: Mapped[bool] = mapped_column(Boolean)  # R24
    replayed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # R24, immutable
    row_version: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )  # R6 optimistic lock
    route: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    match_report_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    risk_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_pack_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    short_pay_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    short_pay_balance: Mapped[str | None] = mapped_column(Text, server_default="NONE")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(_in("action", ("APPROVE", "SHORT_PAY", "REJECT")), name="ck_appr_action"),
        CheckConstraint("length(reason) >= 10", name="ck_appr_reason"),
        UniqueConstraint("execution_id", "actor", "gate_hash", name="uq_appr_one_vote"),
    )

    approval_id: Mapped[str] = mapped_column(Text, primary_key=True)  # the decision_id (R11)
    execution_id: Mapped[str] = mapped_column(ForeignKey("invoice_executions.execution_id"))
    actor: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))  # from session only
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    gate_hash: Mapped[str] = mapped_column(Text)  # approval void if it changes
    voided_at: Mapped[datetime | None] = mapped_column(TS)
    resume_checkpoint_id: Mapped[str] = mapped_column(Text)  # server-read, never from the browser
    resume_interrupt_id: Mapped[str] = mapped_column(Text)  # PRD §11.3
    consumed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class SpendLedger(Base):
    """R8: committed(date) = Σ RESERVE − Σ RELEASE on that business_date."""

    __tablename__ = "spend_ledger"
    __table_args__ = (
        CheckConstraint(_in("kind", ("RESERVE", "RELEASE")), name="ck_spend_kind"),
        CheckConstraint("amount_cents > 0", name="ck_spend_amount"),
        CheckConstraint("(kind = 'RELEASE') = (reserve_entry_id IS NOT NULL)", name="ck_spend_ref"),
        Index("ix_spend_policy_date", "policy_id", "business_date"),
    )

    entry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("invoice_executions.execution_id"))
    policy_id: Mapped[str] = mapped_column(Text)  # version-independent
    vendor_id: Mapped[str] = mapped_column(Text)  # per-vendor caps (G12)
    business_date: Mapped[date] = mapped_column(Date)  # in cap_timezone; RELEASE copies it
    kind: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    reserve_entry_id: Mapped[int | None] = mapped_column(ForeignKey("spend_ledger.entry_id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class POLedger(Base):
    """R17: PO consumption, mirrors spend_ledger."""

    __tablename__ = "po_ledger"
    __table_args__ = (
        CheckConstraint(_in("kind", ("RESERVE", "RELEASE")), name="ck_poled_kind"),
        CheckConstraint("qty >= 0", name="ck_poled_qty"),
        CheckConstraint("amount_cents > 0", name="ck_poled_amount"),
    )

    entry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.po_id"))
    execution_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    qty: Mapped[int] = mapped_column(Integer)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    reserve_entry_id: Mapped[int | None] = mapped_column(ForeignKey("po_ledger.entry_id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class SettlementOutbox(Base):
    """The frozen command (R5); amount and receiver are immutable (trigger: P1-22)."""

    __tablename__ = "settlement_outbox"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_outbox_amount"),
        CheckConstraint(
            _in("status", ("PENDING", "CLAIMED", "DONE", "FAILED")), name="ck_outbox_status"
        ),
    )

    command_id: Mapped[str] = mapped_column(Text, primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_executions.execution_id"), unique=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    receiver_payer_id: Mapped[str] = mapped_column(Text)
    vendor_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    claimed_until: Mapped[datetime | None] = mapped_column(TS)  # lease (R19)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class Payout(Base):
    """Inserted BEFORE the POST (R19)."""

    __tablename__ = "payouts"

    payout_id: Mapped[str] = mapped_column(Text, primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_executions.execution_id"), unique=True
    )
    sender_batch_id: Mapped[str] = mapped_column(Text, unique=True)
    payout_batch_id: Mapped[str | None] = mapped_column(Text, unique=True)
    payout_item_id: Mapped[str | None] = mapped_column(Text, unique=True)
    receiver: Mapped[str] = mapped_column(Text)  # copied from the outbox command
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    paypal_receiver: Mapped[str | None] = mapped_column(
        Text
    )  # as returned; must equal receiver (R5)
    paypal_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    batch_status: Mapped[str | None] = mapped_column(Text)
    item_status: Mapped[str | None] = mapped_column(Text)
    last_polled_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class SettlementControl(Base):
    """R23: single-row halt flag (the row is inserted by the migration)."""

    __tablename__ = "settlement_controls"
    __table_args__ = (CheckConstraint("id", name="ck_controls_single_row"),)

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    halted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    halted_reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime | None] = mapped_column(TS)


class ExecutionEvent(Base):
    """Append-only, single global HMAC chain (R21); written by settlement only."""

    __tablename__ = "execution_events"

    event_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    actor: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(Text)


class AuditQueue(Base):
    """The api appends unsigned rows; settlement drains, signs and appends (R21)."""

    __tablename__ = "audit_queue"

    queue_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    ts: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class AuditAnchor(Base):
    """R21: head hashes published outside the DB."""

    __tablename__ = "audit_anchors"

    anchor_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_seq: Mapped[int] = mapped_column(BigInteger)
    head_hash: Mapped[str] = mapped_column(Text)
    external_ref: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (CheckConstraint(_in("app", ("TREASURY", "VENDOR")), name="ck_webhook_app"),)

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)  # PayPal event id: natural dedupe
    app: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[str] = mapped_column(Text)
    signature_verified: Mapped[bool] = mapped_column(Boolean)
    simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    headers_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_body: Mapped[bytes | None] = mapped_column(LargeBinary)  # full only when verified (R25)
    confirmed_by_get: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # R10
    received_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
    processed_at: Mapped[datetime | None] = mapped_column(TS)
