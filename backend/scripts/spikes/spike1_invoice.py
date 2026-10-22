"""SPIKE-1 (PRD §18): Vendor app creates + sends an invoice to the treasury email, invoicer = vendor account.
Pass: invoice status SENT/UNPAID, invoicer = VENDOR_ACCOUNT_EMAIL. Run: python spike1_invoice.py"""
import sys

from common import Sandbox


def main() -> int:
    sandbox = Sandbox()
    token, scopes = sandbox.token("VENDOR")
    print("invoicing scope on vendor token:", any(s.endswith("/invoicing") for s in scopes))
    invoice_id = sandbox.new_invoice(token)
    status, body = sandbox.invoice_state(token, invoice_id, "get")
    invoicer = body.get("invoicer", {}).get("email_address")
    ok = status == 200 and body.get("status") in ("SENT", "UNPAID") and invoicer == sandbox.env["VENDOR_ACCOUNT_EMAIL"]
    print("SPIKE-1", "PASS" if ok else "FAIL", "| invoice_id:", invoice_id)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
