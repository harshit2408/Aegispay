"""R13: every mandate leaf has a server identity or reviewed bounded value."""

import json
from types import SimpleNamespace

import pytest

from app.agents.mandate_compiler import HardLimits, MandateCompileError, clamp, compile_mandate
from tests.unit.test_policy_gate import policy


def test_every_org_clamp_and_deterministic_diff():
    draft = policy(
        max_single_invoice_cents=10**9,
        daily_cap_cents=10**9,
        vendor_daily_cap_cents=10**9,
        cap_timezone="Etc/GMT+12",
        max_price_variance_bps=99999,
        max_short_pay_delta_cents=10**9,
        dual_approval_risk_threshold=1000,
        short_pay_assumptions={
            "claim_ops_cost_cents": 10**9,
            "notice_ops_cost_cents": 10**9,
            "contest_cost_cents": 10**9,
            "claim_days": 1000,
            "p_recover": 1,
            "annual_cost_of_capital": 0,
            "p_vendor_contests": 1,
        },
    )
    calls = []

    def complete(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(content=draft.model_dump_json())

    result = compile_mandate(
        "pay all",
        policy_id="server",
        version=2,
        active=policy(),
        complete=complete,
        limits=HardLimits(),
    )
    compiled = result["policy"]
    assert (compiled.policy_id, compiled.version) == ("server", 2)
    assert compiled.max_single_invoice_cents == 100000
    assert compiled.daily_cap_cents == 500000
    assert compiled.vendor_daily_cap_cents == 150000
    assert compiled.cap_timezone == "UTC"
    assert compiled.max_price_variance_bps == 200
    assert compiled.max_short_pay_delta_cents == 20000
    assert compiled.dual_approval_risk_threshold == 80
    assert compiled.short_pay_assumptions.claim_days == 365
    for field in ("claim_ops_cost_cents", "notice_ops_cost_cents", "contest_cost_cents"):
        assert getattr(compiled.short_pay_assumptions, field) == 20000
        assert "short_pay_assumptions." + field in result["clamped_fields"]
    assert result["diff"]["policy_id"] == {"before": "pol-1", "after": "server"}
    assert calls[0][0][0] == "A1"
    assert "tools" not in calls[0][1]
    assert "BEGIN_DOCUMENT_" in calls[0][0][1][1]["content"]


@pytest.mark.parametrize(
    "changes",
    [
        {"max_single_invoice_cents": -1},
        {"daily_cap_cents": 1.5},
        {"short_pay_assumptions": {"p_recover": 1.01}},
        {"short_pay_assumptions": {"annual_cost_of_capital": -0.1}},
        {"short_pay_assumptions": {"p_vendor_contests": 2}},
        {"short_pay_assumptions": {"claim_days": -1}},
        {"short_pay_assumptions": {"arbitrary": 1}},
        {"activate": True},
    ],
)
def test_invalid_fields_fail_closed(changes):
    raw = {**policy().model_dump(), **changes}
    with pytest.raises(MandateCompileError):
        compile_mandate(
            "rules",
            policy_id="server",
            version=1,
            active=None,
            complete=lambda *a, **kw: SimpleNamespace(content=json.dumps(raw)),
            limits=HardLimits(),
        )


def test_boolean_false_and_reviewed_timezone_preserved_and_threshold_ceiling80():
    result, fields = clamp(
        policy(
            auto_short_pay_enabled=False,
            cap_timezone="Asia/Kolkata",
            dual_approval_risk_threshold=90,
        ),
        HardLimits(dual_approval_risk_threshold=100),
    )
    assert not result.auto_short_pay_enabled and result.cap_timezone == "Asia/Kolkata"
    assert result.dual_approval_risk_threshold == 80
    assert fields == ["dual_approval_risk_threshold"]
