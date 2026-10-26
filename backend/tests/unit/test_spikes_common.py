import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import common  # noqa: E402


def test_load_env_strips_quotes_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        'PAYPAL_BASE_URL="https://api-m.sandbox.paypal.com"\n'
        "VENDOR_ACCOUNT_EMAIL=supplier@x.example   # inline note\n"
        "EMPTY=\n"
        "SECRET=a=b=c\n",
        encoding="utf-8",
    )
    env = common.load_env(env_file)
    assert env["PAYPAL_BASE_URL"] == "https://api-m.sandbox.paypal.com"
    assert env["VENDOR_ACCOUNT_EMAIL"] == "supplier@x.example"
    assert env["EMPTY"] == ""
    assert env["SECRET"] == "a=b=c"
    assert "# comment" not in env


def test_new_invoice_sends_amounts_as_strings_and_vendor_as_invoicer(monkeypatch):
    sandbox = common.Sandbox(
        {
            "PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com/",
            "VENDOR_ACCOUNT_EMAIL": "v@x",
            "TREASURY_ACCOUNT_EMAIL": "t@x",
        }
    )
    sent = []

    def fake_call(method, path, **kw):
        sent.append((method, path, kw))
        return 201, "dbg", {"href": "https://x/v2/invoicing/invoices/INV2-ABC"}

    monkeypatch.setattr(sandbox, "call", fake_call)
    invoice_id = sandbox.new_invoice("tok", total="10.00", partial=True)
    assert invoice_id == "INV2-ABC"
    body = sent[0][2]["body"]
    assert body["invoicer"]["email_address"] == "v@x"
    assert body["items"][0]["unit_amount"]["value"] == "10.00"
    assert body["configuration"]["partial_payment"]["minimum_amount_due"]["value"] == "1.00"
    assert sent[0][2]["request_id"].startswith("aegis-spike-")
    assert sent[1][1].endswith("/send") and sent[1][2]["body"]["send_to_recipient"] is False


def test_payout_body_amount_string_and_recipient_type():
    sandbox = common.Sandbox({"PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com"})
    body = sandbox.payout_body("PAYERID123", "1.00", "s10", recipient_type="PAYPAL_ID")
    item = body["items"][0]
    assert item["recipient_type"] == "PAYPAL_ID" and item["receiver"] == "PAYERID123"
    assert item["amount"] == {"value": "1.00", "currency": "USD"}
    assert body["sender_batch_header"]["sender_batch_id"] == f"AEGIS-SPIKE-s10-{sandbox.run}"
    assert sandbox.payout_body("v@x", "1.00", "s3")["items"][0]["recipient_type"] == "EMAIL"


def test_payout_posts_with_request_id_and_returns_batch_id(monkeypatch):
    sandbox = common.Sandbox({"PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com"})
    sent = []

    def fake_call(method, path, **kw):
        sent.append((method, path, kw))
        return 201, "dbg", {"batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING"}}

    monkeypatch.setattr(sandbox, "call", fake_call)
    assert sandbox.payout("tok", "v@x", "1.00", "s3") == "B1"
    assert sent[0][:2] == ("POST", "/v1/payments/payouts")
    assert sent[0][2]["request_id"] == f"aegis-spike-s3-{sandbox.run}"


@pytest.mark.parametrize(
    "url",
    [
        "https://api-m.paypal.com",  # live
        "https://api.paypal.com",  # live (legacy host)
        "http://api-m.sandbox.paypal.com",  # plain HTTP
        "https://api-m.sandbox.paypal.com.evil.example",  # lookalike suffix
        "https://evil.example/api-m.sandbox.paypal.com",  # lookalike path
        "https://user@api-m.sandbox.paypal.com",  # userinfo
        "https://api-m.sandbox.paypal.com:8443",  # other port
        "",
        None,
    ],
)
def test_sandbox_refuses_non_sandbox_base_url_before_any_request(monkeypatch, url):
    def no_network(*a, **kw):
        raise AssertionError("network used before the base URL was validated")

    monkeypatch.setattr(common._OPENER, "open", no_network)
    with pytest.raises(SystemExit):
        common.Sandbox(
            {"PAYPAL_BASE_URL": url, "VENDOR_CLIENT_ID": "id", "VENDOR_CLIENT_SECRET": "s"}
        )


def test_sandbox_does_not_follow_redirects():
    handler = common._NoRedirect()
    assert (
        handler.redirect_request(None, None, 302, "Found", {}, "https://api-m.paypal.com/") is None
    )
    assert any(isinstance(h, common._NoRedirect) for h in common._OPENER.handlers)


def test_call_returns_redirect_as_error_instead_of_following(monkeypatch):
    sandbox = common.Sandbox({"PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com"})
    seen = []

    def fake_open(req, timeout):
        seen.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 302, "Found", {}, None)

    monkeypatch.setattr(common._OPENER, "open", fake_open)
    status, _, _ = sandbox.call("POST", "/v1/oauth2/token", basic="id:secret", form="x")
    assert status == 302
    assert seen == ["https://api-m.sandbox.paypal.com/v1/oauth2/token"]
