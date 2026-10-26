"""SPIKE-4 (PRD §18): record a payment on a vendor invoice, partial then full; print the resulting statuses.
Known result: partial -> PARTIALLY_PAID; full -> MARKED_AS_PAID (not PAID). After the payments it lists the
Vendor app's webhook events that mention the invoice (needs the P0-14 subscription), to record which event each
payment produced. Run: python spike4_record_payment.py"""

import json
import sys
import time

from common import Sandbox

FULLY_PAID = ("PAID", "MARKED_AS_PAID")
EVENT_WAIT_SECONDS = 150  # the events list lags delivery by minutes; 45 s returned nothing


def invoice_events(sandbox, token, invoice_id, since):
    """(create_time, event_type, snapshot status, snapshot payment amounts) for events mentioning the invoice.
    The snapshot is the invoice as of delivery, not as of the payment that triggered the event."""
    path = f"/v1/notifications/webhooks-events?page_size=100&start_time={since}"
    status, _, body = sandbox.brief("list webhook events", sandbox.call("GET", path, token=token))
    if status != 200:
        return None
    rows = []
    for e in body.get("events", []):
        resource = e.get("resource", {})
        if invoice_id not in json.dumps(resource):
            continue
        invoice = resource.get("invoice", resource)
        paid = [t["amount"]["value"] for t in invoice.get("payments", {}).get("transactions", [])]
        rows.append((e["create_time"], e["event_type"], invoice.get("status"), paid))
    return sorted(rows)


def main() -> int:
    sandbox = Sandbox()
    token, _ = sandbox.token("VENDOR")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
    invoice_id = sandbox.new_invoice(token, total="10.00", partial=True)
    sandbox.invoice_state(token, invoice_id, "after send")
    statuses = []
    for amount in ("4.00", "6.00"):
        sandbox.brief(
            f"record payment {amount}",
            sandbox.call(
                "POST",
                f"/v2/invoicing/invoices/{invoice_id}/payments",
                token=token,
                body={
                    "method": "PAYPAL",
                    "payment_date": time.strftime("%Y-%m-%d"),
                    "amount": {"currency_code": "USD", "value": amount},
                    "note": "spike-4",
                },
                request_id=f"aegis-spike4-{sandbox.run}-{amount}",
            ),
        )
        _, body = sandbox.invoice_state(token, invoice_id, f"after {amount}")
        statuses.append(body.get("status"))
    time.sleep(EVENT_WAIT_SECONDS)
    print("webhook events for this invoice:", invoice_events(sandbox, token, invoice_id, since))
    ok = statuses[0] == "PARTIALLY_PAID" and statuses[1] in FULLY_PAID
    print("SPIKE-4", "PASS" if ok else "FAIL", "| statuses:", statuses)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
