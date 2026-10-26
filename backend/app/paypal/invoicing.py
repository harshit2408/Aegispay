"""PayPal Invoicing v2 for the Vendor app (PRD §9.2).

Importable only from `vendor_sim/` (tests/unit/test_import_boundary.py). Every value read back is
vendor-asserted: money is parsed to integer cents (R4) and anything unparsable is recorded in
`Invoice.issues` so the match node can freeze (G14) instead of guessing. Vendor free-text fields
(note, memo, terms, item names) are deliberately not exposed here (R14).
"""

import re
from dataclasses import dataclass
from datetime import date

from app.paypal.client import PayPalClient
from app.paypal.money import cents_to_str, str_to_cents

CURRENCY = "USD"
OPEN_STATUSES = frozenset({"SENT", "UNPAID", "PARTIALLY_PAID"})  # R20
PAID_STATUSES = frozenset({"PAID", "MARKED_AS_PAID"})  # SPIKE-4: both mean fully paid
_INVOICE_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_INVOICE_HREF = re.compile(r"https://[A-Za-z0-9.-]+/v2/invoicing/invoices/([A-Za-z0-9-]{1,64})")
_INTEGER = re.compile(r"[0-9]{1,9}")
NOTE_MAX = 400


@dataclass(frozen=True)
class InvoiceSpec:
    """A draft invoice from the Vendor Simulator (§9.2). Single line item, USD."""

    invoice_number: str
    reference: str
    note: str
    invoicer_email: str
    recipient_email: str
    item_name: str
    quantity: int
    unit_cents: int
    partial_minimum_cents: int | None = None

    def __post_init__(self) -> None:
        for name in ("quantity", "unit_cents"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive int")
        if self.partial_minimum_cents is not None and (
            type(self.partial_minimum_cents) is not int or self.partial_minimum_cents <= 0
        ):
            raise ValueError("partial_minimum_cents must be a positive int")

    def body(self) -> dict:
        body: dict = {
            "detail": {
                "invoice_number": self.invoice_number,
                "reference": self.reference,
                "currency_code": CURRENCY,
                "note": self.note,
                "payment_term": {"term_type": "NET_10"},
            },
            "invoicer": {"email_address": self.invoicer_email},
            "primary_recipients": [{"billing_info": {"email_address": self.recipient_email}}],
            "items": [
                {
                    "name": self.item_name,
                    "quantity": str(self.quantity),
                    "unit_amount": {
                        "currency_code": CURRENCY,
                        "value": cents_to_str(self.unit_cents),
                    },
                    "unit_of_measure": "QUANTITY",
                }
            ],
        }
        if self.partial_minimum_cents is not None:
            body["configuration"] = {
                "partial_payment": {
                    "allow_partial_payment": True,
                    "minimum_amount_due": {
                        "currency_code": CURRENCY,
                        "value": cents_to_str(self.partial_minimum_cents),
                    },
                }
            }
        return body


@dataclass(frozen=True)
class InvoiceItem:
    quantity: int | None
    unit_cents: int | None


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    status: str | None
    invoice_number: str | None
    reference: str | None
    currency: str | None
    invoicer_email: str | None
    total_cents: int | None
    paid_cents: int | None
    due_cents: int | None
    items: tuple[InvoiceItem, ...]
    issues: tuple[str, ...]

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_fully_paid(self) -> bool:
        return self.status in PAID_STATUSES


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _money(node: object, label: str, issues: list[str]) -> int | None:
    amount = _dict(node)
    if amount.get("currency_code") != CURRENCY:
        issues.append(f"{label}: currency is not {CURRENCY}")
        return None
    try:
        return str_to_cents(amount.get("value"))  # type: ignore[arg-type]
    except ValueError:
        issues.append(f"{label}: not an exact USD amount")
        return None


def _quantity(raw: object, index: int, issues: list[str]) -> int | None:
    if isinstance(raw, str) and _INTEGER.fullmatch(raw) and int(raw) > 0:
        return int(raw)
    issues.append(f"items[{index}].quantity: not a positive integer")
    return None


def parse_invoice(invoice_id: str, body: dict) -> Invoice:
    issues: list[str] = []
    detail = _dict(body.get("detail"))
    raw_items = body.get("items")
    items = []
    for i, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
        raw = _dict(raw)
        items.append(
            InvoiceItem(
                _quantity(raw.get("quantity"), i, issues),
                _money(raw.get("unit_amount"), f"items[{i}].unit_amount", issues),
            )
        )
    if not items:
        issues.append("items: missing")
    status = body.get("status")
    if not isinstance(status, str):
        issues.append("status: missing")
        status = None
    paid = _dict(body.get("payments")).get("paid_amount")
    due = body.get("due_amount")
    return Invoice(
        invoice_id=invoice_id,
        status=status,
        invoice_number=detail.get("invoice_number"),
        reference=detail.get("reference"),
        currency=detail.get("currency_code"),
        invoicer_email=_dict(body.get("invoicer")).get("email_address"),
        total_cents=_money(body.get("amount"), "amount", issues),
        paid_cents=_money(paid, "paid_amount", issues) if paid else 0,
        due_cents=_money(due, "due_amount", issues) if due else None,
        items=tuple(items),
        issues=tuple(issues),
    )


def _check_id(value: str) -> str:
    if not _INVOICE_ID.fullmatch(value):
        raise ValueError("invoice id must match [A-Za-z0-9-]{1,64}")
    return value


class Invoicing:
    def __init__(self, client: PayPalClient) -> None:
        self._client = client

    async def get(self, invoice_id: str, *, execution_id: str | None = None) -> Invoice:
        _check_id(invoice_id)
        resp = await self._client.get(
            f"/v2/invoicing/invoices/{invoice_id}", execution_id=execution_id
        )
        return parse_invoice(invoice_id, resp.body)

    async def create_draft(self, spec: InvoiceSpec, *, request_id: str) -> str:
        resp = await self._client.post(
            "/v2/invoicing/invoices", request_id=request_id, json=spec.body()
        )
        href = resp.body.get("href")
        match = _INVOICE_HREF.fullmatch(href) if isinstance(href, str) else None
        if not match:
            raise ValueError("create-invoice response carries no invoice link")
        return match[1]

    async def send(self, invoice_id: str, *, request_id: str) -> None:
        await self._client.post(
            f"/v2/invoicing/invoices/{_check_id(invoice_id)}/send",
            request_id=request_id,
            json={"send_to_invoicer": False, "send_to_recipient": False},
        )

    async def record_payment(
        self,
        invoice_id: str,
        *,
        execution_id: str,
        amount_cents: int,
        payment_date: date,
        note: str,
    ) -> None:
        """R7: after confirmed payout success only; keyed so a retry cannot double-record."""
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive int")
        await self._client.post(
            f"/v2/invoicing/invoices/{_check_id(invoice_id)}/payments",
            request_id=f"aegis-{execution_id}-recordpay",
            json={
                "method": "PAYPAL",
                "payment_date": payment_date.isoformat(),
                "amount": {"currency_code": CURRENCY, "value": cents_to_str(amount_cents)},
                "note": note.strip()[:NOTE_MAX],
            },
            execution_id=execution_id,
        )
