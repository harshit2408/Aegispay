"""P4-03: documented SPIKE-11 EMAIL error; synthetic GETs enforce fail-closed recovery."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.paypal.client import PayPalClient
from app.paypal.payouts import PayoutCommand, PayoutMismatch, Payouts

BASE = "https://api-m.sandbox.paypal.com"
BATCH = "BATCHSPIKE11"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "paypal" / "spike11-duplicate.json"


@pytest.mark.parametrize("confirmation", ["matching", "missing_items", "wrong_receiver"])
async def test_recorded_duplicate_error_requires_frozen_command_get_match(confirmation):
    command = PayoutCommand("contract-spike11", "PAYER12345678", 100)
    # Only the duplicate error is transcribed from a recorded response; this GET is synthetic.
    batch = {
        "batch_header": {
            "payout_batch_id": BATCH,
            "sender_batch_header": {"sender_batch_id": command.sender_batch_id},
        },
        "items": [
            {
                "payout_item_id": "ITEM1",
                "transaction_status": "PENDING",
                "payout_item": command.body()["items"][0],
            }
        ],
    }
    if confirmation == "missing_items":
        batch.pop("items")
    elif confirmation == "wrong_receiver":
        batch["items"][0]["payout_item"]["receiver"] = "OTHERPAYER123"

    with respx.mock as router:
        router.post(f"{BASE}/v1/oauth2/token").respond(
            200, json={"access_token": "synthetic-token", "expires_in": 3600}
        )
        payout = router.post(f"{BASE}/v1/payments/payouts").respond(
            400, json=json.loads(FIXTURE.read_text(encoding="utf-8"))
        )
        lookup = router.get(f"{BASE}/v1/payments/payouts/{BATCH}").respond(200, json=batch)
        async with httpx.AsyncClient() as http:
            client = PayPalClient(
                app="TREASURY",
                base_url=BASE,
                client_id="synthetic",
                client_secret="synthetic",
                http=http,
            )
            payouts = Payouts(client)
            if confirmation == "matching":
                adopted = await payouts.create_or_adopt(command)
                assert (adopted.payout_batch_id, adopted.payout_item_id, adopted.adopted) == (
                    BATCH,
                    "ITEM1",
                    True,
                )
            else:
                with pytest.raises(PayoutMismatch):
                    await payouts.create_or_adopt(command)
        assert payout.call_count == lookup.call_count == 1
        assert payout.calls[0].request.headers["paypal-request-id"] == command.request_id
        assert lookup.calls[0].request.url.host == "api-m.sandbox.paypal.com"
