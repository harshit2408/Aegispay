"""SPIKE-11 (PRD §18): crash recovery for a payout whose response was lost.
1. Create a 1.00 USD batch (request id R1).
2. Re-POST the same body with the SAME request id R1 (crash retry per R5): does PayPal replay the batch?
3. Re-POST the same sender_batch_id with a NEW request id R2: record the full error body; does it carry
   the existing payout_batch_id?
4. Try lookups by sender_batch_id: list payouts, Transaction Search.
Pass: the existing batch id is obtainable from step 2, 3, 4a or 4b. Transaction Search (4c) is printed
only: it can lag ~3h, so a fresh batch is not expected there and PASS never depends on it. Moves 1.00 USD (2.00 if PayPal accepts a
duplicate, which would itself be a finding). Run: python spike11_recovery.py"""

import json
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from common import SPIKE_AMOUNT, Sandbox


def main() -> int:
    sandbox = Sandbox()
    token, _ = sandbox.token("TREASURY")
    body = sandbox.payout_body(sandbox.env["VENDOR_ACCOUNT_EMAIL"], SPIKE_AMOUNT, "s11")
    sender_batch_id = body["sender_batch_header"]["sender_batch_id"]
    r1, r2 = f"aegis-spike-s11-{sandbox.run}", f"aegis-spike-s11-{sandbox.run}-new"
    print("sender_batch_id:", sender_batch_id)

    def post(label, request_id):
        return sandbox.brief(
            label,
            sandbox.call(
                "POST", "/v1/payments/payouts", token=token, body=body, request_id=request_id
            ),
        )

    status, _, resp = post("1 create (R1)", r1)
    if status >= 400:
        return 1
    batch_id = resp["batch_header"]["payout_batch_id"]
    print("  batch_id:", batch_id)
    found = {}

    status, _, resp = post("2 re-POST same request id (R1)", r1)
    replay_id = resp.get("batch_header", {}).get("payout_batch_id")
    print("  replayed batch_id:", replay_id, "| same:", replay_id == batch_id)
    found["same request id replay"] = replay_id == batch_id

    status, _, resp = post("3 re-POST same sender_batch_id, new request id (R2)", r2)
    print("  full body:", json.dumps(resp))
    dup_id = resp.get("batch_header", {}).get("payout_batch_id")
    if status < 400:
        print("  DUPLICATE ACCEPTED, new batch_id:", dup_id, "| same:", dup_id == batch_id)
    found["duplicate error body"] = status >= 400 and batch_id in json.dumps(resp)

    for label, path in [
        ("4a list by sender_batch_id", f"/v1/payments/payouts?sender_batch_id={sender_batch_id}"),
        ("4b list payouts", "/v1/payments/payouts"),
    ]:
        status, _, resp = sandbox.brief(label, sandbox.call("GET", path, token=token))
        hit = status == 200 and batch_id in json.dumps(resp)
        print("  keys:", sorted(resp)[:10], "| contains batch_id:", hit)
        found[label] = hit

    now = datetime.now(UTC)
    query = urlencode(
        {
            "start_date": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fields": "all",
            "page_size": "100",
        }
    )
    status, _, resp = sandbox.brief(
        "4c transaction search (last hour)",
        sandbox.call("GET", f"/v1/reporting/transactions?{query}", token=token),
    )
    text = json.dumps(resp)
    print(
        "  transactions:",
        len(resp.get("transaction_details", [])),
        "| contains sender_batch_id:",
        sender_batch_id in text,
        "| contains batch_id:",
        batch_id in text,
    )
    print("  note: Transaction Search can lag ~3h; informational only, not part of PASS")

    print("paths that yield the existing batch id:", json.dumps(found))
    ok = any(found.values())
    print("SPIKE-11", "PASS" if ok else "FAIL (fallback NEEDS_MANUAL_REVIEW)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
