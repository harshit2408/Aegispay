"""SPIKE-2 (PRD §18): can Treasury credentials read an invoice addressed to the treasury?
Creates a fresh vendor invoice, reads it with both tokens. 403 with no `invoicing` scope on the treasury
token is INCONCLUSIVE (Invoicing not enabled on that app), not a no. Run: python spike2_treasury_read.py"""
import sys

from common import Sandbox


def main() -> int:
    sandbox = Sandbox()
    vendor_token, _ = sandbox.token("VENDOR")
    treasury_token, treasury_scopes = sandbox.token("TREASURY")
    has_scope = any(s.endswith("/invoicing") for s in treasury_scopes)
    invoice_id = sandbox.new_invoice(vendor_token)
    sandbox.invoice_state(vendor_token, invoice_id, "vendor view")
    status, _ = sandbox.invoice_state(treasury_token, invoice_id, "treasury view")
    print("invoicing scope on treasury token:", has_scope)
    if status == 200:
        print("SPIKE-2 PASS: treasury can read; PRD §3 boundary can narrow")
        return 0
    verdict = "INCONCLUSIVE (enable Invoicing on the treasury app, rerun)" if not has_scope else "FAIL (use Vendor gateway)"
    print("SPIKE-2", verdict)
    return 1


if __name__ == "__main__":
    sys.exit(main())
