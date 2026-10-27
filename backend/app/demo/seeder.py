"""P1-10: trusted demo records and resumable Vendor invoice setup (PRD §14.4).

The operator supplies the verified identity and Argon2id hashes. No document or invoice can onboard
a payee. The local manifest journals draft IDs before sending; this module never submits payouts.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.core import (
    GoodsReceipt,
    MandatePolicy,
    Operator,
    PurchaseOrder,
    VendorMaster,
    VendorMasterChange,
)
from app.schemas.case import MandatePolicy as CompiledMandatePolicy
from app.vendor_sim.gateway import InvoiceSpec, VendorGateway

OPERATORS = {
    "controller1": "controller",
    "controller2": "controller",
    "cfo": "cfo",
    "receiver": "receiver",
    "vendor_admin1": "vendor_admin",
    "vendor_admin2": "vendor_admin",
    "admin": "admin",
}
SCENARIOS = (
    ("S1", "1042", "LiDAR bracket", 100, 480, 100),
    ("S2", "1043", "Servo motor", 100, 500, 85),
    ("S3", "1044", "Flight controller", 80, 500, 80),
)
VENDOR_ID = "demo-supplier"


class DemoSeed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    run_id: str = Field(pattern=r"^[a-z0-9-]{1,12}$")
    vendor_payer_id: str = Field(pattern=r"^[A-Z0-9]{13}$")
    vendor_email: str
    treasury_email: str
    onboarding_evidence: dict[str, Any]
    operator_hashes: dict[str, str] = Field(repr=False)

    @field_validator("vendor_email", "treasury_email")
    @classmethod
    def email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("a sandbox account email is required")
        return value

    @field_validator("onboarding_evidence")
    @classmethod
    def evidence(cls, value: dict) -> dict:
        if not value.get("method") or not value.get("reference"):
            raise ValueError("identity evidence needs method and reference from trusted onboarding")
        return value

    @field_validator("operator_hashes")
    @classmethod
    def hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != set(OPERATORS) or any(
            not re.fullmatch(
                r"\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+", h
            )
            for h in value.values()
        ):
            raise ValueError("supply an Argon2id hash for each of the seven named operators")
        return value


def assert_demo(settings: Settings) -> None:
    if (
        not settings.demo_enabled
        or settings.demo_replay_mode
        or settings.paypal_base_url
        not in (
            "https://api-m.sandbox.paypal.com",
            "https://api.sandbox.paypal.com",
        )
    ):
        raise ValueError("seeding requires DEMO_ENABLED, sandbox PayPal, and replay disabled")


def manifest_for(config: DemoSeed) -> dict:
    return {
        "run_id": config.run_id,
        "vendor_id": VENDOR_ID,
        "policy_id": f"demo-{config.run_id}",
        "scenarios": {
            name: {
                "po_id": f"PO-{number}-{config.run_id}",
                "grn_id": f"GRN-{number}-{config.run_id}",
                "invoice_number": f"AP-{number}-{config.run_id}",
                "billed_cents": qty * price,
                "accepted_cents": accepted * price,
                "paypal_invoice_id": None,
            }
            for name, number, _, qty, price, accepted in SCENARIOS
        },
    }


def _save(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def seed_demo(
    sessions: async_sessionmaker[AsyncSession],
    *,
    config: DemoSeed,
    settings: Settings,
    manifest_dir: Path,
    gateway: VendorGateway | None = None,
) -> dict:
    """Commit local trust roots, then optionally create/send S1/S2 invoices through VendorGateway.

    Reusing a run is idempotent. A different vendor identity is an error, never an implicit change.
    Keep the manifest directory for retries; PayPal's own idempotency retention is bounded.
    """
    assert_demo(settings)
    expected = manifest_for(config)
    path = manifest_dir / f"{config.run_id}.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else expected
    comparable = json.loads(json.dumps(manifest))
    for row in comparable.get("scenarios", {}).values():
        row["paypal_invoice_id"] = None
    if comparable != expected:
        raise ValueError("existing demo manifest does not match this run")
    now = datetime.now(UTC)
    onboarded = now - timedelta(days=90)
    async with sessions() as s, s.begin():
        for name, role in OPERATORS.items():
            operator_id = f"demo-{name}"
            operator = await s.get(Operator, operator_id)
            if operator is None:
                s.add(
                    Operator(
                        operator_id=operator_id,
                        display_name=name,
                        role=role,
                        password_hash=config.operator_hashes[name],
                    )
                )
            elif operator.role != role or operator.disabled:
                raise ValueError("existing demo operator role/disabled state differs")
        await s.flush()
        vendor = await s.get(VendorMaster, VENDOR_ID)
        if vendor is None:
            s.add(
                VendorMaster(
                    vendor_id=VENDOR_ID,
                    vendor_name="Demo precision supplier",
                    paypal_payer_id=config.vendor_payer_id,
                    verified_paypal_email=config.vendor_email,
                    trust_status="VERIFIED",
                    version=1,
                    onboarded_at=onboarded,
                    onboarding_evidence=config.onboarding_evidence,
                    payee_last_changed_at=onboarded + timedelta(hours=72),
                )
            )
            await s.flush()
            s.add(
                VendorMasterChange(
                    change_id="demo-onboarding",
                    vendor_id=VENDOR_ID,
                    old_values={},
                    new_values={
                        "paypal_payer_id": config.vendor_payer_id,
                        "verified_paypal_email": config.vendor_email,
                    },
                    requested_by="demo-vendor_admin1",
                    approved_by="demo-vendor_admin2",
                    approved_at=onboarded,
                    effective_at=onboarded + timedelta(hours=72),
                    status="EFFECTIVE",
                    created_at=onboarded,
                )
            )
        elif (
            vendor.paypal_payer_id != config.vendor_payer_id
            or vendor.verified_paypal_email != config.vendor_email
            or vendor.trust_status != "VERIFIED"
        ):
            raise ValueError(
                "demo identity differs from approved vendor master; use change control"
            )
        policy_id = expected["policy_id"]
        if await s.get(MandatePolicy, (policy_id, 1)) is None:
            active = await s.scalar(select(MandatePolicy).where(MandatePolicy.is_active))
            if active and not active.policy_id.startswith("demo-"):
                raise ValueError("cannot replace a non-demo active mandate")
            await s.execute(
                update(MandatePolicy).where(MandatePolicy.is_active).values(is_active=False)
            )
            compiled = CompiledMandatePolicy(
                policy_id=policy_id,
                version=1,
                max_single_invoice_cents=50_000,
                daily_cap_cents=200_000,
                vendor_daily_cap_cents=150_000,
            )
            s.add(
                MandatePolicy(
                    policy_id=policy_id,
                    version=1,
                    raw_nl_prompt="Sandbox demo fixture",
                    compiled_json=compiled.model_dump(mode="json"),
                    max_single_invoice_cents=compiled.max_single_invoice_cents,
                    daily_cap_cents=compiled.daily_cap_cents,
                    cap_timezone=compiled.cap_timezone,
                    max_price_variance_bps=compiled.max_price_variance_bps,
                    auto_short_pay_enabled=compiled.auto_short_pay_enabled,
                    max_short_pay_delta_cents=compiled.max_short_pay_delta_cents,
                    dual_approval_risk_threshold=compiled.dual_approval_risk_threshold,
                    created_by="demo-controller1",
                    activated_by="demo-cfo",
                    activated_at=now,
                    is_active=True,
                )
            )
        await s.flush()
        for name, number, item, qty, price, accepted in SCENARIOS:
            row = expected["scenarios"][name]
            if await s.get(PurchaseOrder, row["po_id"]) is None:
                s.add(
                    PurchaseOrder(
                        po_id=row["po_id"],
                        vendor_id=VENDOR_ID,
                        sku_id=f"SKU-{number}",
                        item_name=item,
                        ordered_qty=qty,
                        unit_price_cents=price,
                        created_by="demo-admin",
                    )
                )
                await s.flush()
                s.add(
                    GoodsReceipt(
                        grn_id=row["grn_id"],
                        po_id=row["po_id"],
                        received_qty=qty,
                        accepted_qty=accepted,
                        rejected_qty=qty - accepted,
                        inspection_note="crushed in transit" if name == "S2" else "Accepted",
                        recorded_by="demo-receiver",
                        source="SEEDED",
                        received_at=now,
                    )
                )
    _save(path, manifest)
    if gateway is not None:
        for name, number, item, qty, price, _ in SCENARIOS[:2]:
            row = manifest["scenarios"][name]
            if row["paypal_invoice_id"] is None:
                row["paypal_invoice_id"] = await gateway.create_draft(
                    InvoiceSpec(
                        invoice_number=row["invoice_number"],
                        reference=row["po_id"],
                        note=f"AegisPay sandbox {name}",
                        invoicer_email=config.vendor_email,
                        recipient_email=config.treasury_email,
                        item_name=item,
                        quantity=qty,
                        unit_cents=price,
                        partial_minimum_cents=100 if name == "S2" else None,
                    ),
                    request_id=f"aegis-demo-{config.run_id}-{number}-new",
                )
                _save(path, manifest)
            invoice = await gateway.get_invoice(row["paypal_invoice_id"])
            if invoice.status == "DRAFT":
                await gateway.send(
                    row["paypal_invoice_id"], request_id=f"aegis-demo-{config.run_id}-{number}-send"
                )
            elif invoice.status not in (
                "SENT",
                "UNPAID",
                "PARTIALLY_PAID",
                "PAID",
                "MARKED_AS_PAID",
            ):
                raise ValueError("demo invoice is not in a resumable state")
    return manifest
