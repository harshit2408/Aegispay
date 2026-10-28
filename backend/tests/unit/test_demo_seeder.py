"""Sandbox seed invariants and mocked vendor setup; no provider network access."""

import json
from argparse import Namespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.demo import __main__ as cli
from app.demo.seeder import OPERATORS, DemoSeed, assert_demo, manifest_for, seed_demo
from app.models.core import GoodsReceipt, MandatePolicy, Operator, PurchaseOrder, VendorMasterChange
from app.schemas.case import MandatePolicy as CompiledMandatePolicy
from app.vendor_sim.gateway import VendorGateway

HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$YWJjZGVmZ2hpamtsbW5vcA"


def config(run_id="run1", **kw):
    return DemoSeed(
        **{
            "run_id": run_id,
            "vendor_payer_id": "PAYER12345678",
            "vendor_email": "v@x.example",
            "treasury_email": "t@x.example",
            "onboarding_evidence": {
                "method": "vendor-owned unapproved order",
                "reference": "ORDER-TEST",
            },
            "operator_hashes": dict.fromkeys(OPERATORS, HASH),
            **kw,
        }
    )


def settings(**kw):
    return Settings(
        _env_file=None, database_url="postgresql+asyncpg://unused", demo_enabled=True, **kw
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"demo_enabled": False},
        {"demo_replay_mode": True},
        {"paypal_base_url": "https://api-m.paypal.com"},
        {"paypal_base_url": "https://api-m.sandbox.paypal.com.attacker.example"},
    ],
)
def test_seed_guard_requires_explicit_sandbox(changes):
    opts = {"demo_enabled": True, **changes}
    with pytest.raises(ValueError):
        assert_demo(Settings(_env_file=None, database_url="unused", **opts))


def test_run_suffix_and_trusted_identity_are_validated():
    with pytest.raises(ValueError):
        config("../escape")
    with pytest.raises(ValueError):
        config(vendor_payer_id="invoice asserted receiver")
    with pytest.raises(ValueError):
        config(onboarding_evidence={})
    private_input = "do-not-log-operator-password"
    with pytest.raises(ValueError) as error:
        config(operator_hashes=dict.fromkeys(OPERATORS, private_input))
    assert private_input not in str(error.value)
    manifest = manifest_for(config())
    assert manifest["scenarios"]["S1"]["accepted_cents"] == 48_000
    assert manifest["scenarios"]["S2"]["accepted_cents"] == 42_500
    assert manifest["scenarios"]["S3"]["paypal_invoice_id"] is None


async def test_cli_defaults_to_dry_run_and_requires_both_write_flags(monkeypatch, tmp_path):
    path = tmp_path / "operators.json"
    path.write_text(config().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(cli, "Settings", lambda **kw: settings())
    database = AsyncMock(side_effect=AssertionError("dry run opened the database"))
    monkeypatch.setattr(cli, "get_sessionmaker", database)
    args = Namespace(config=path, manifest_dir=tmp_path, apply_db=False, send_invoices=False)
    assert (await cli.run(args))["run_id"] == "run1"
    database.assert_not_called()
    args.send_invoices = True
    with pytest.raises(ValueError, match="also requires"):
        await cli.run(args)


async def test_seed_is_idempotent_and_keeps_distinct_sod_actors(pg_sessionmaker, tmp_path):
    for _ in range(2):
        manifest = await seed_demo(
            pg_sessionmaker, config=config(), settings=settings(), manifest_dir=tmp_path
        )
    async with pg_sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Operator)) == 7
        assert await s.scalar(select(func.count()).select_from(PurchaseOrder)) == 3
        assert await s.scalar(select(func.count()).select_from(GoodsReceipt)) == 3
        change = (await s.scalars(select(VendorMasterChange))).one()
        assert change.requested_by != change.approved_by
        assert change.status == "EFFECTIVE"
        receipt = await s.get(GoodsReceipt, manifest["scenarios"]["S2"]["grn_id"])
        po = await s.get(PurchaseOrder, receipt.po_id)
        assert receipt.source == "SEEDED" and receipt.recorded_by == "demo-receiver"
        assert receipt.recorded_by != po.created_by
        assert receipt.accepted_qty == 85 and receipt.rejected_qty == 15
        policy = await s.get(MandatePolicy, (manifest["policy_id"], 1))
        compiled = CompiledMandatePolicy.model_validate(policy.compiled_json)
        assert (compiled.policy_id, compiled.version) == (policy.policy_id, policy.version)
        assert compiled.max_single_invoice_cents == policy.max_single_invoice_cents == 50_000
        assert compiled.daily_cap_cents == policy.daily_cap_cents == 200_000
        assert compiled.vendor_daily_cap_cents == 150_000
        for field in (
            "cap_timezone",
            "max_price_variance_bps",
            "auto_short_pay_enabled",
            "max_short_pay_delta_cents",
            "dual_approval_risk_threshold",
        ):
            assert getattr(compiled, field) == getattr(policy, field)
        assert compiled.cap_timezone == "UTC" and compiled.max_price_variance_bps == 0
        assert compiled.auto_short_pay_enabled and compiled.max_short_pay_delta_cents == 10_000
        assert compiled.dual_approval_risk_threshold == 80


async def test_new_run_preserves_old_rows_and_rejects_payee_change(pg_sessionmaker, tmp_path):
    for run in ("run1", "run2"):
        await seed_demo(
            pg_sessionmaker, config=config(run), settings=settings(), manifest_dir=tmp_path
        )
    async with pg_sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(PurchaseOrder)) == 6
    with pytest.raises(ValueError, match="identity differs"):
        await seed_demo(
            pg_sessionmaker,
            config=config(vendor_payer_id="OTHER12345678"),
            settings=settings(),
            manifest_dir=tmp_path,
        )


async def test_invoice_setup_resumes_saved_draft_and_never_creates_s3(pg_sessionmaker, tmp_path):
    gateway = AsyncMock(spec=VendorGateway)
    gateway.create_draft.side_effect = ["INV2-S1", "INV2-S2"]
    invoice = AsyncMock()
    invoice.status = "DRAFT"
    gateway.get_invoice.return_value = invoice
    gateway.send.side_effect = [RuntimeError("interrupted"), None, None]
    with pytest.raises(RuntimeError, match="interrupted"):
        await seed_demo(
            pg_sessionmaker,
            config=config(),
            settings=settings(),
            manifest_dir=tmp_path,
            gateway=gateway,
        )
    saved = json.loads((tmp_path / "run1.json").read_text())
    assert saved["scenarios"]["S1"]["paypal_invoice_id"] == "INV2-S1"
    await seed_demo(
        pg_sessionmaker,
        config=config(),
        settings=settings(),
        manifest_dir=tmp_path,
        gateway=gateway,
    )
    assert gateway.create_draft.await_count == 2
    assert gateway.send.await_args_list[0] == gateway.send.await_args_list[1]
    assert {c.args[0].reference for c in gateway.create_draft.await_args_list} == {
        "PO-1042-run1",
        "PO-1043-run1",
    }
