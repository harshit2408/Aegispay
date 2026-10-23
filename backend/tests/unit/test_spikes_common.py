import sys
from pathlib import Path

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
            "PAYPAL_BASE_URL": "https://x",
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
    sandbox = common.Sandbox({"PAYPAL_BASE_URL": "https://x"})
    body = sandbox.payout_body("PAYERID123", "1.00", "s10", recipient_type="PAYPAL_ID")
    item = body["items"][0]
    assert item["recipient_type"] == "PAYPAL_ID" and item["receiver"] == "PAYERID123"
    assert item["amount"] == {"value": "1.00", "currency": "USD"}
    assert body["sender_batch_header"]["sender_batch_id"] == f"AEGIS-SPIKE-s10-{sandbox.run}"
    assert sandbox.payout_body("v@x", "1.00", "s3")["items"][0]["recipient_type"] == "EMAIL"


def test_payout_posts_with_request_id_and_returns_batch_id(monkeypatch):
    sandbox = common.Sandbox({"PAYPAL_BASE_URL": "https://x"})
    sent = []

    def fake_call(method, path, **kw):
        sent.append((method, path, kw))
        return 201, "dbg", {"batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING"}}

    monkeypatch.setattr(sandbox, "call", fake_call)
    assert sandbox.payout("tok", "v@x", "1.00", "s3") == "B1"
    assert sent[0][:2] == ("POST", "/v1/payments/payouts")
    assert sent[0][2]["request_id"] == f"aegis-spike-s3-{sandbox.run}"
