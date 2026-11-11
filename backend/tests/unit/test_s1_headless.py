import copy
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
        "po_consumption": {"paid_cents": 48000, "accepted_value_cents": 48000, "reserved_cents": 0},
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


import argparse  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000", "https://api.example"],
)
def test_api_base_allows_https_or_loopback_http(url):
    assert s1.api_base(url) == url


@pytest.mark.parametrize(
    "url", ["http://api.example", "http://10.0.0.5:8000", "ftp://127.0.0.1", "127.0.0.1:8000"]
)
def test_api_base_refuses_cleartext_remote(url):
    with pytest.raises(SystemExit):
        s1.api_base(url)


def test_session_cookie_found_by_name_among_other_cookies(monkeypatch, tmp_path):
    seen = {}

    def handler(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"csrf_token": "csrf"},
                headers=[
                    ("set-cookie", "other=nope; Path=/"),
                    ("set-cookie", "aegis_session=tok; Secure; HttpOnly; Path=/"),
                ],
            )
        seen[request.url.path] = request.headers.get("cookie")
        if request.url.path == "/api/documents":
            return httpx.Response(202, json={"execution_id": "ex1"})
        return httpx.Response(200, json={**_good(), "status": "SETTLED"})

    real = httpx.Client
    monkeypatch.setattr(
        s1.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setenv("AEGIS_OPERATOR_ID", "op")
    monkeypatch.setenv("AEGIS_OPERATOR_PASSWORD", "pw")
    monkeypatch.delenv("AEGIS_API_URL", raising=False)
    pdf = tmp_path / "s1.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    args = argparse.Namespace(
        pdf=str(pdf), po="PO-1", invoice="INV-1", out=str(tmp_path / "o.json"), timeout=5, grace=0
    )
    assert s1.run(args) == 0
    assert seen["/api/documents"] == "aegis_session=tok"


def _mutations():
    def drop(step):
        return lambda c: c["settlement_timeline"].__delitem__(
            [e["status"] for e in c["settlement_timeline"]].index(step)
        )

    def flag(step, **over):
        def apply(case):
            for event in case["settlement_timeline"]:
                if event["status"] == step:
                    event.update(over)

        return apply

    cases = {
        "duplicate": lambda c: c.update(duplicate_of_execution_id="e0"),
        "short_paid": lambda c: c["po_consumption"].update(paid_cents=42500),
        "po_not_closed_value": lambda c: c["po_consumption"].update(accepted_value_cents=50000),
        "po_cents_still_reserved": lambda c: c["po_consumption"].update(reserved_cents=1),
        "no_consumption": lambda c: c.update(po_consumption=None),
        "no_invoice": lambda c: c.update(paypal_invoice=None),
        "invoice_partially_paid": lambda c: c["paypal_invoice"].update(status="PARTIALLY_PAID"),
        "no_anchor": lambda c: c.update(audit_anchor=None),
        "unverified_anchor": lambda c: c["audit_anchor"].update(verified=False),
    }
    for status in (s1.TERMINAL - {"SETTLED"}) | {"APPROVED_FOR_PAYMENT", "PAYOUT_SUBMITTED"}:
        cases[f"status_{status}"] = lambda c, s=status: c.update(status=s)
    for step in ("PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED", "SETTLED"):
        cases[f"missing_{step}"] = drop(step)
        cases[f"unverified_{step}"] = flag(step, verified=False)
        cases[f"simulated_{step}"] = flag(step, simulated=True)
    return cases


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_every_wrong_state_is_rejected(name):
    case = copy.deepcopy(_good())
    _mutations()[name](case)
    assert s1.verdict(case) != []


def test_an_empty_case_is_never_a_pass():
    assert len(s1.verdict({})) >= 5
