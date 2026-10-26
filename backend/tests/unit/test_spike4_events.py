import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import spike4_record_payment  # noqa: E402


class FakeSandbox:
    def __init__(self, status, body):
        self.res = (status, "dbg", body)

    def call(self, method, path, **kw):
        return self.res

    def brief(self, label, res):
        return res


def event(invoice_id, kind):
    invoice = {
        "id": invoice_id,
        "status": "MARKED_AS_PAID",
        "payments": {"transactions": [{"amount": {"value": "4.00"}}]},
    }
    return {
        "create_time": "2026-10-26T00:00:00Z",
        "event_type": kind,
        "resource": {"invoice": invoice},
    }


def test_invoice_events_keeps_only_this_invoice_and_reads_the_snapshot():
    body = {
        "events": [
            event("INV2-A", "INVOICING.INVOICE.PAID"),
            event("INV2-B", "INVOICING.INVOICE.PAID"),
        ]
    }
    rows = spike4_record_payment.invoice_events(
        FakeSandbox(200, body), "tok", "INV2-A", "2026-10-26T00:00:00Z"
    )
    assert rows == [("2026-10-26T00:00:00Z", "INVOICING.INVOICE.PAID", "MARKED_AS_PAID", ["4.00"])]


def test_invoice_events_returns_none_when_the_list_call_fails():
    assert spike4_record_payment.invoice_events(FakeSandbox(403, {}), "tok", "INV2-A", "x") is None
