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
        pdf=str(pdf), po="PO-1", invoice="INV-1", out=str(tmp_path / "o.json"), timeout=5
    )
    assert s1.run(args) == 0
    assert seen["/api/documents"] == "aegis_session=tok"
