"""SPIKE-10 (PRD §18): read the supplier's payer ID, pay 1.00 USD by recipient_type=PAYPAL_ID, and record
whether the create response and the GET batch/item responses echo receiver + amount.
Payer ID source, first that works: vendor token userinfo (payer_id), vendor-created order payee.merchant_id,
VENDOR_PAYER_ID in .env (dashboard). Pass: item SUCCESS. Sends a real sandbox payout of 1.00 USD.
Run: python spike10_payer_id.py"""

import json
import sys

from common import SPIKE_AMOUNT, Sandbox

TERMINAL_ITEM = {"SUCCESS", "FAILED", "DENIED", "RETURNED", "BLOCKED", "UNCLAIMED"}


def read_payer_id(sandbox):
    token, _ = sandbox.token("VENDOR")
    status, _, body = sandbox.brief(
        "userinfo (vendor token)",
        sandbox.call("GET", "/v1/identity/oauth2/userinfo?schema=paypalv1.1", token=token),
    )
    if status == 200:
        emails = [e.get("value") for e in body.get("emails", [])]
        print("  userinfo keys:", sorted(body))
        print(
            "  payer_id:",
            body.get("payer_id"),
            "| email matches VENDOR_ACCOUNT_EMAIL:",
            sandbox.env["VENDOR_ACCOUNT_EMAIL"] in emails,
        )
        if body.get("payer_id"):
            return body["payer_id"]
    # A client_credentials userinfo has only sub/user_id (not a payer id). An order the vendor
    # creates (never approved, so no money moves) names the vendor as payee with merchant_id.
    status, _, order = sandbox.brief(
        "create order (vendor token, not approved)",
        sandbox.call(
            "POST",
            "/v2/checkout/orders",
            token=token,
            body={
                "intent": "CAPTURE",
                "purchase_units": [{"amount": {"currency_code": "USD", "value": SPIKE_AMOUNT}}],
            },
            request_id=f"aegis-spike-s10-order-{sandbox.run}",
        ),
    )
    if status < 400 and order.get("id"):
        status, _, order = sandbox.brief(
            "GET order", sandbox.call("GET", f"/v2/checkout/orders/{order['id']}", token=token)
        )
    if status == 200:
        payee = (order.get("purchase_units") or [{}])[0].get("payee", {})
        print(
            "  order payee merchant_id:",
            payee.get("merchant_id"),
            "| email matches VENDOR_ACCOUNT_EMAIL:",
            payee.get("email_address") == sandbox.env["VENDOR_ACCOUNT_EMAIL"],
        )
        if payee.get("merchant_id"):
            return payee["merchant_id"]
    payer_id = sandbox.env.get("VENDOR_PAYER_ID")
    print("  fallback VENDOR_PAYER_ID from .env:", payer_id or "MISSING")
    return payer_id


def echo(label, item, payer_id):
    """Print which receiver/amount fields a response item carries and whether they match the command."""
    fields = {k: item.get(k) for k in ("recipient_type", "receiver", "amount")}
    print(f"  {label} item fields: {json.dumps(fields)}")
    print(
        f"  {label} receiver == payer_id: {item.get('receiver') == payer_id}"
        f" | amount == {SPIKE_AMOUNT} USD:"
        f" {item.get('amount') == {'value': SPIKE_AMOUNT, 'currency': 'USD'}}"
    )


def main() -> int:
    sandbox = Sandbox()
    payer_id = read_payer_id(sandbox)
    if not payer_id:
        print("SPIKE-10 FAIL: no payer id")
        return 1
    token, _ = sandbox.token("TREASURY")
    body = sandbox.payout_body(payer_id, SPIKE_AMOUNT, "s10", recipient_type="PAYPAL_ID")
    status, _, resp = sandbox.brief(
        "create payout PAYPAL_ID",
        sandbox.call(
            "POST",
            "/v1/payments/payouts",
            token=token,
            body=body,
            request_id=f"aegis-spike-s10-{sandbox.run}",
        ),
    )
    if status >= 400:
        print("SPIKE-10 FAIL: create rejected; fallback is EMAIL (R1)")
        return 1
    print(
        "  create response keys:",
        sorted(resp),
        "| batch_header keys:",
        sorted(resp["batch_header"]),
    )
    echo("create", (resp.get("items") or [{}])[0].get("payout_item", {}), payer_id)
    batch_id = resp["batch_header"]["payout_batch_id"]
    item_id, last = sandbox.poll_payout(token, batch_id, TERMINAL_ITEM)
    _, _, batch = sandbox.call("GET", f"/v1/payments/payouts/{batch_id}", token=token)
    batch_item = (batch.get("items") or [{}])[0]
    print("  GET batch item keys:", sorted(batch_item))
    echo("GET batch", batch_item.get("payout_item", {}), payer_id)
    if item_id:
        _, _, item = sandbox.call("GET", f"/v1/payments/payouts-item/{item_id}", token=token)
        echo("GET item", item.get("payout_item", {}), payer_id)
        print("  GET item payout_item_fee:", item.get("payout_item_fee"))
    else:
        print("  no payout_item_id seen while polling; GET item skipped")
    ok = last is not None and last[1] == "SUCCESS"
    print("SPIKE-10", "PASS" if ok else "FAIL", "| final (batch, item):", last)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
