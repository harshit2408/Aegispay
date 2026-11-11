import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "s1_headless", Path(__file__).parents[2] / "scripts" / "s1_headless.py"
)
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)


def _event(status, **over):
    return {"status": status, "verified": True, "simulated": False, **over}


def _good():
    return {
        "status": "SETTLED",
        "duplicate_of_execution_id": None,
        "po_consumption": {"paid_cents": 48000},
        "settlement_timeline": [
            _event("PAYOUT_SUBMITTED"),
            _event("PAYOUT_SUCCEEDED"),
            _event("SETTLED"),
        ],
        "paypal_invoice": {"status": "MARKED_AS_PAID"},
        "audit_anchor": {"verified": True},
    }


def test_clean_settled_case_passes():
    assert s1.verdict(_good()) == []


def test_simulated_event_is_not_sandbox_evidence():
    case = _good()
    case["settlement_timeline"][1] = _event("PAYOUT_SUCCEEDED", simulated=True)
    assert any("PAYOUT_SUCCEEDED" in p for p in s1.verdict(case))


def test_wrong_amount_status_and_invoice_all_reported():
    case = _good()
    case["status"] = "FROZEN"
    case["po_consumption"]["paid_cents"] = 47999
    case["paypal_invoice"]["status"] = "SENT"
    assert len(s1.verdict(case)) == 3
