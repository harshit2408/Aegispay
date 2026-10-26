import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import common  # noqa: E402
import spike10_payer_id as spike10  # noqa: E402


@pytest.mark.parametrize(
    ("source", "field", "value"),
    [
        (None, None, None),
        ("batch", "http_status", 404),
        ("item", "http_status", 503),
        ("batch", "receiver", "OTHER-PAYER"),
        ("item", "receiver", "OTHER-PAYER"),
        ("batch", "amount", {"value": "2.00", "currency": "USD"}),
        ("item", "amount", {"value": "1.00", "currency": "EUR"}),
        ("batch", "recipient_type", "EMAIL"),
        ("item", "recipient_type", "EMAIL"),
        ("batch", "transaction_status", "FAILED"),
        ("item", "transaction_status", "RETURNED"),
        ("poll", "item_id", None),
    ],
)
def test_verdict_requires_successful_matching_batch_and_item_gets(
    monkeypatch, source, field, value
):
    payout_item = {
        "recipient_type": "PAYPAL_ID",
        "receiver": "PAYERID123",
        "amount": {"value": "1.00", "currency": "USD"},
    }
    responses = {
        name: {"transaction_status": "SUCCESS", "payout_item": copy.deepcopy(payout_item)}
        for name in ("batch", "item")
    }
    statuses = {"batch": 200, "item": 200}
    item_id = "I1"
    if source == "poll":
        item_id = value
    elif field == "http_status":
        statuses[source] = value
    elif field == "transaction_status":
        responses[source][field] = value
    elif source:
        responses[source]["payout_item"][field] = value

    class FakeSandbox:
        run = "offline"
        payout_body = common.Sandbox.payout_body
        brief = staticmethod(lambda label, response: response)

        def token(self, app):
            assert app == "TREASURY"
            return "fake-token", []

        def poll_payout(self, *args):
            return item_id, ("SUCCESS", "SUCCESS")

        def call(self, method, path, **kwargs):
            if method == "POST":
                assert kwargs["body"]["items"][0] == {
                    **payout_item,
                    "sender_item_id": "spike-s10-offline-1",
                    "note": "spike s10",
                }
                assert kwargs["request_id"] == "aegis-spike-s10-offline"
                return 201, "fake-debug", {"batch_header": {"payout_batch_id": "B1"}}
            if path == "/v1/payments/payouts/B1":
                return statuses["batch"], "fake-debug", {"items": [responses["batch"]]}
            assert path == "/v1/payments/payouts-item/I1"
            return statuses["item"], "fake-debug", responses["item"]

    monkeypatch.setattr(spike10, "Sandbox", FakeSandbox)
    monkeypatch.setattr(spike10, "read_payer_id", lambda sandbox: "PAYERID123")
    assert spike10.main() == (0 if source is None else 1)
