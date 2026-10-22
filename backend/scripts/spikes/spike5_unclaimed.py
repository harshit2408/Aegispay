"""SPIKE-5 (PRD §18): payout to an email with no PayPal account -> UNCLAIMED; then try to cancel the item.
Known result: UNCLAIMED works; cancel returns 400 BATCH_NOT_COMPLETED until the batch is SUCCESS, then 500
INTERNAL_SERVICE_ERROR. Exit 0 = UNCLAIMED reached (the cancel result is printed, not asserted).
Run: python spike5_unclaimed.py"""
import sys

from common import Sandbox


def main() -> int:
    sandbox = Sandbox()
    token, _ = sandbox.token("TREASURY")
    batch_id = sandbox.payout(token, f"nobody-{sandbox.run}@example.com", "1.00", "s5")
    item_id, last = sandbox.poll_payout(
        token, batch_id, {"UNCLAIMED", "SUCCESS", "FAILED", "DENIED", "RETURNED", "BLOCKED"}, until_batch={"SUCCESS"})
    if last is None or last[1] != "UNCLAIMED":
        print("SPIKE-5 FAIL: item did not reach UNCLAIMED, got", last)
        return 1
    status, _, _ = sandbox.brief("cancel item", sandbox.call(
        "POST", f"/v1/payments/payouts-item/{item_id}/cancel", token=token))
    _, _, item = sandbox.call("GET", f"/v1/payments/payouts-item/{item_id}", token=token)
    print("item after cancel attempt:", item.get("transaction_status"))
    print("SPIKE-5 UNCLAIMED PASS | cancel", "WORKS" if status < 400 else f"NOT WORKING (HTTP {status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
