"""Identity, mandates, vendors, POs, goods receipts (PRD §8). Postgres only (ADR-002)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

TS = DateTime(timezone=True)
NOW = func.now()


class Operator(Base):
    """R18: named people, one role each."""

    __tablename__ = "operators"
    __table_args__ = (
        CheckConstraint(
            "role IN ('controller','cfo','receiver','vendor_admin','admin')",
            name="ck_operator_role",
        ),
    )

    operator_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)  # argon2id
    disabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    failed_logins: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(TS)


class MandatePolicy(Base):
    """Immutable, versioned; exactly one active version (R13)."""

    __tablename__ = "mandate_policies"
    __table_args__ = (
        CheckConstraint("max_single_invoice_cents >= 0", name="ck_mandate_max_single"),
        CheckConstraint("daily_cap_cents >= 0", name="ck_mandate_daily_cap"),
        CheckConstraint(
            "activated_by IS NULL OR activated_by <> created_by",
            name="ck_mandate_author_not_activator",
        ),
        Index("one_active_mandate", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    policy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_nl_prompt: Mapped[str] = mapped_column(Text)
    compiled_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    clamped_fields: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    max_single_invoice_cents: Mapped[int] = mapped_column(BigInteger)
    daily_cap_cents: Mapped[int] = mapped_column(BigInteger)
    cap_timezone: Mapped[str] = mapped_column(Text, server_default="UTC")
    max_price_variance_bps: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    auto_short_pay_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    max_short_pay_delta_cents: Mapped[int] = mapped_column(BigInteger, server_default=text("10000"))
    dual_approval_risk_threshold: Mapped[int] = mapped_column(Integer, server_default=text("80"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_by: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))
    activated_by: Mapped[str | None] = mapped_column(ForeignKey("operators.operator_id"))
    activated_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class VendorMaster(Base):
    """Current row = latest effective change (R15). Payee comes only from here (R1)."""

    __tablename__ = "vendor_master"
    __table_args__ = (
        CheckConstraint("trust_status IN ('VERIFIED','COOLING','BLOCKED')", name="ck_vendor_trust"),
    )

    vendor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vendor_name: Mapped[str] = mapped_column(Text)
    paypal_payer_id: Mapped[str] = mapped_column(Text, unique=True)
    verified_paypal_email: Mapped[str] = mapped_column(Text, unique=True)
    trust_status: Mapped[str] = mapped_column(Text, server_default="VERIFIED")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    onboarded_at: Mapped[datetime] = mapped_column(TS)
    onboarding_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payee_last_changed_at: Mapped[datetime | None] = mapped_column(TS)


class VendorMasterChange(Base):
    """Append-only payee-change history (R15). Immutability trigger lands with P1-20."""

    __tablename__ = "vendor_master_changes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','COOLING','EFFECTIVE','REJECTED')", name="ck_vmc_status"
        ),
        CheckConstraint("approved_by IS NULL OR approved_by <> requested_by", name="ck_vmc_sod"),
    )

    change_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendor_master.vendor_id"))
    old_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requested_by: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("operators.operator_id"))
    approved_at: Mapped[datetime | None] = mapped_column(TS)
    effective_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


class _POColumns:
    """Shared by `purchase_orders` and `po_versions` ("same shape", PRD §8)."""

    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendor_master.vendor_id"))
    sku_id: Mapped[str] = mapped_column(Text)
    item_name: Mapped[str] = mapped_column(Text)
    ordered_qty: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text, server_default="USD")
    status: Mapped[str] = mapped_column(Text, server_default="OPEN")
    created_by: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)


def _po_checks(prefix: str) -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint("ordered_qty > 0", name=f"ck_{prefix}_qty"),
        CheckConstraint("unit_price_cents > 0", name=f"ck_{prefix}_price"),
        CheckConstraint("currency = 'USD'", name=f"ck_{prefix}_currency"),
        CheckConstraint("status IN ('OPEN','CLOSED')", name=f"ck_{prefix}_status"),
    )


class PurchaseOrder(_POColumns, Base):
    """Single line per PO in v2; admin import only. Edits create a new version."""

    __tablename__ = "purchase_orders"
    __table_args__ = _po_checks("po")

    po_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class POVersion(_POColumns, Base):
    """Superseded PO versions; approvals bind to a version."""

    __tablename__ = "po_versions"
    __table_args__ = _po_checks("pov")

    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.po_id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


class GoodsReceipt(Base):
    """The receiving leg; append-only, corrections supersede (R16). Triggers land with P1-19."""

    __tablename__ = "goods_receipts"
    __table_args__ = (
        CheckConstraint("received_qty >= 0", name="ck_grn_received"),
        CheckConstraint("accepted_qty >= 0", name="ck_grn_accepted"),
        CheckConstraint("rejected_qty >= 0", name="ck_grn_rejected"),
        CheckConstraint("source IN ('RECEIVER_SESSION','SEEDED')", name="ck_grn_source"),
        CheckConstraint("received_at <= now()", name="ck_grn_not_future"),
        CheckConstraint("accepted_qty + rejected_qty = received_qty", name="ck_grn_balance"),
    )

    grn_id: Mapped[str] = mapped_column(Text, primary_key=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.po_id"))
    supersedes_grn_id: Mapped[str | None] = mapped_column(ForeignKey("goods_receipts.grn_id"))
    received_qty: Mapped[int] = mapped_column(Integer)
    accepted_qty: Mapped[int] = mapped_column(Integer)
    rejected_qty: Mapped[int] = mapped_column(Integer)
    inspection_note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("operators.operator_id"))
    source: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=NOW)
