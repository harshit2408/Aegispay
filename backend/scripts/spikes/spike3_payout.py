"""SPIKE-3 (PRD §18): payout Treasury -> supplier; record batch/item status transitions with timings.
Sends a real sandbox payout of 1.00 USD. Pass: item reaches SUCCESS. Webhook latency needs a webhook (P0-14).
Run: python spike3_payout.py"""
import sys

from common import Sandbox

TERMINAL_ITEM = {"SUCCESS", "FAILED", "DENIED", "RETURNED", "BLOCKED", "UNCLAIMED"}


def main() -> int:
    sandbox = Sandbox()
    token, _ = sandbox.token("TREASURY")
    batch_id = sandbox.payout(token, sandbox.env["VENDOR_ACCOUNT_EMAIL"], "1.00", "s3")
    _, last = sandbox.poll_payout(token, batch_id, TERMINAL_ITEM, until_batch={"SUCCESS", "DENIED", "CANCELED"})
    ok = last is not None and last[1] == "SUCCESS"
    print("SPIKE-3", "PASS" if ok else "FAIL", "| final (batch, item):", last)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
