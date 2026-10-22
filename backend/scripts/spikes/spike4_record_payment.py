"""SPIKE-4 (PRD §18): record a payment on a vendor invoice, partial then full; print the resulting statuses.
Known result: partial -> PARTIALLY_PAID; full -> MARKED_AS_PAID (not PAID). Webhook events need P0-14.
Run: python spike4_record_payment.py"""
import sys
import time

from common import Sandbox

FULLY_PAID = ("PAID", "MARKED_AS_PAID")


def main() -> int:
    sandbox = Sandbox()
    token, _ = sandbox.token("VENDOR")
    invoice_id = sandbox.new_invoice(token, total="10.00", partial=True)
    sandbox.invoice_state(token, invoice_id, "after send")
    statuses = []
    for amount in ("4.00", "6.00"):
        sandbox.brief(f"record payment {amount}", sandbox.call(
            "POST", f"/v2/invoicing/invoices/{invoice_id}/payments", token=token,
            body={"method": "PAYPAL", "payment_date": time.strftime("%Y-%m-%d"),
                  "amount": {"currency_code": "USD", "value": amount}, "note": "spike-4"},
            request_id=f"aegis-spike4-{sandbox.run}-{amount}"))
        _, body = sandbox.invoice_state(token, invoice_id, f"after {amount}")
        statuses.append(body.get("status"))
    ok = statuses[0] == "PARTIALLY_PAID" and statuses[1] in FULLY_PAID
    print("SPIKE-4", "PASS" if ok else "FAIL", "| statuses:", statuses)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
