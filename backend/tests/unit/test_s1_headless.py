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
        "gate": {"route": "AUTO_SETTLE"},
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


def _s2():
    case = copy.deepcopy(_good())
    case["gate"] = {"route": "SHORT_PAY"}
    case["po_consumption"] = {
        "paid_cents": 42500,
        "accepted_value_cents": 42500,
        "reserved_cents": 0,
    }
    case["paypal_invoice"] = {"status": "PARTIALLY_PAID"}
    case["short_pay"] = {
        "expected_cost_pay_and_claim_cents": 6592,
        "expected_cost_short_pay_cents": 1500,
        "chosen": "SHORT_PAY",
    }
    return case


def test_s2_short_pay_case_passes_and_is_not_an_s1_pass():
    assert s1.verdict(_s2(), "S2") == []
    assert s1.verdict(_s2(), "S1") != []
    assert s1.verdict(_good(), "S2") != []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(gate={"route": "AUTO_SETTLE"}),
        lambda c: c["po_consumption"].update(paid_cents=50000),
        lambda c: c["paypal_invoice"].update(status="PAID"),
        lambda c: c["short_pay"].update(expected_cost_short_pay_cents=1501),
        lambda c: c["short_pay"].update(expected_cost_pay_and_claim_cents=6591),
        lambda c: c["short_pay"].update(chosen="PAY_AND_CLAIM"),
        lambda c: c.update(short_pay=None),
    ],
)
def test_s2_wrong_state_is_rejected(mutate):
    case = _s2()
    mutate(case)
    assert s1.verdict(case, "S2") != []


def _dup(**over):
    return {
        "status": "DUPLICATE_BLOCKED",
        "duplicate_of_execution_id": "orig",
        "settlement_timeline": [],
    } | over


def test_s4_duplicate_must_be_fast_linked_and_unpaid():
    assert s1.verdict_duplicate(_dup(), "orig", 0.2) == []
    assert s1.verdict_duplicate(_dup(), "orig", 1.0) != []
    assert s1.verdict_duplicate(_dup(duplicate_of_execution_id="other"), "orig", 0.2) != []
    assert s1.verdict_duplicate(_dup(duplicate_of_execution_id=None), "orig", 0.2) != []
    assert s1.verdict_duplicate(_dup(status="FROZEN"), "orig", 0.2) != []
    assert (
        s1.verdict_duplicate(
            _dup(settlement_timeline=[{"status": "PAYOUT_SUBMITTED"}]), "orig", 0.2
        )
        != []
    )
    assert len(s1.verdict_duplicate({}, "orig", 0.0)) >= 2


def test_s4_run_blocks_immediately_and_reports_elapsed(monkeypatch, tmp_path):
    def handler(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"csrf_token": "csrf"},
                headers=[("set-cookie", "aegis_session=tok; Secure; HttpOnly; Path=/")],
            )
        if request.url.path == "/api/documents":
            return httpx.Response(202, json={"execution_id": "dup1"})
        return httpx.Response(200, json=_dup())

    real = httpx.Client
    monkeypatch.setattr(
        s1.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setenv("AEGIS_OPERATOR_ID", "op")
    monkeypatch.setenv("AEGIS_OPERATOR_PASSWORD", "pw")
    monkeypatch.delenv("AEGIS_API_URL", raising=False)
    pdf = tmp_path / "s1.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = tmp_path / "o.json"
    args = argparse.Namespace(
        scenario="S4", original="orig", pdf=str(pdf), po="PO-1", invoice="INV-1",
        out=str(out), timeout=5, grace=0,
    )  # fmt: skip
    assert s1.run(args) == 0
    assert 0 <= __import__("json").loads(out.read_text())["elapsed_seconds"] < 1
    args.original = "someone-else"
    assert s1.run(args) == 1


def test_pdf_builder_scenarios(tmp_path):
    maker = importlib.util.spec_from_file_location(
        "s1_make_pdf", Path(__file__).parents[2] / "scripts" / "s1_make_pdf.py"
    )
    module = importlib.util.module_from_spec(maker)
    maker.loader.exec_module(module)
    import pdfplumber

    for scenario, number, total in (("S1", "1042", "$480.00"), ("S2", "1043", "$500.00")):
        out = tmp_path / f"{scenario}.pdf"
        module.build(out, run="p215a", payee_email="v@example.com", scenario=scenario)
        with pdfplumber.open(out) as pdf:
            text = pdf.pages[0].extract_text()
        assert f"AP-{number}-p215a" in text and f"PO-{number}-p215a" in text and total in text
