"""PayPal Payouts v1 for the Treasury app (PRD §9.3).

R2: importable only from `settlement/` (tests/unit/test_import_boundary.py). Nothing here accepts a
payee from anywhere but a `PayoutCommand`, which settlement builds from the frozen outbox row (R1).
"""

import re
from dataclasses import dataclass

from app.paypal.client import PayPalClient, PayPalError
from app.paypal.money import cents_to_str, str_to_cents

RECIPIENT_TYPE = "PAYPAL_ID"
CURRENCY = "USD"
NOTE_MAX = 200
_EXECUTION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PAYER_ID = re.compile(r"[A-Za-z0-9]{5,32}")
_PAYPAL_ID = re.compile(r"[A-Za-z0-9]{1,64}")
# SPIKE-11: the duplicate-batch error links the existing batch. Only the id is used, never the URL.
_BATCH_LINK = re.compile(
    r"https://(?:api-m|api)\.(?:sandbox\.)?paypal\.com/v1/payments/payouts/([A-Z0-9]+)"
)


class PayoutMismatch(Exception):
    """A batch/item PayPal reports differs from the frozen command (R5): halt, never adopt."""

    def __init__(self, message: str, payout_batch_id: str | None = None) -> None:
        super().__init__(message)
        self.payout_batch_id = payout_batch_id


class PayoutUnresolved(Exception):
    """Outcome unknown (no parsable batch id, malformed response). Money may have moved (R19)."""


@dataclass(frozen=True)
class PayoutCommand:
    """The frozen settlement command. Keys derive from `execution_id` only (R5)."""

    execution_id: str
    receiver_payer_id: str
    amount_cents: int
    note: str = ""

    def __post_init__(self) -> None:
        if not _EXECUTION_ID.fullmatch(self.execution_id):
            raise ValueError("execution_id must match [A-Za-z0-9_-]{1,64}")
        if not _PAYER_ID.fullmatch(self.receiver_payer_id):
            raise ValueError("receiver must be a PayPal payer ID, not an email or free text")
        if type(self.amount_cents) is not int or self.amount_cents <= 0:
            raise ValueError("amount_cents must be a positive int")

    @property
    def sender_batch_id(self) -> str:
        return f"AEGIS-{self.execution_id}"

    @property
    def sender_item_id(self) -> str:
        return f"{self.execution_id}-1"

    @property
    def request_id(self) -> str:
        return f"aegis-{self.execution_id}-payout"

    def body(self) -> dict:
        note = self.note.strip()[:NOTE_MAX]
        return {
            "sender_batch_header": {
                "sender_batch_id": self.sender_batch_id,
                "email_subject": note or "Payment from AegisPay",
                "email_message": "Paid by AegisPay after PO and goods-receipt match.",
            },
            "items": [
                {
                    "recipient_type": RECIPIENT_TYPE,
                    "receiver": self.receiver_payer_id,
                    "amount": {"value": cents_to_str(self.amount_cents), "currency": CURRENCY},
                    "sender_item_id": self.sender_item_id,
                    "note": note,
                }
            ],
        }


@dataclass(frozen=True)
class PayoutItem:
    payout_item_id: str | None
    status: str | None
    payout_batch_id: str | None
    recipient_type: str | None
    receiver: str | None
    amount_cents: int | None  # None when PayPal's string is not an exact USD amount
    currency: str | None
    sender_item_id: str | None

    def matches(self, cmd: PayoutCommand) -> bool:
        return (
            self.recipient_type == RECIPIENT_TYPE
            and self.receiver == cmd.receiver_payer_id
            and self.amount_cents == cmd.amount_cents
            and self.currency == CURRENCY
            and self.sender_item_id == cmd.sender_item_id
        )


@dataclass(frozen=True)
class PayoutBatch:
    payout_batch_id: str
    batch_status: str | None
    sender_batch_id: str | None
    items: tuple[PayoutItem, ...]

    def matches(self, cmd: PayoutCommand) -> bool:
        return (
            self.sender_batch_id == cmd.sender_batch_id
            and len(self.items) == 1
            and self.items[0].matches(cmd)
        )


@dataclass(frozen=True)
class PayoutRef:
    payout_batch_id: str
    payout_item_id: str | None
    adopted: bool


def _check_id(value: str) -> str:
    if not _PAYPAL_ID.fullmatch(value):
        raise ValueError("PayPal id must be alphanumeric")
    return value


def _parse_item(raw: dict, payout_batch_id: str | None) -> PayoutItem:
    detail = raw.get("payout_item")
    detail = detail if isinstance(detail, dict) else {}
    amount = detail.get("amount")
    amount = amount if isinstance(amount, dict) else {}
    try:
        cents = str_to_cents(amount.get("value"))  # type: ignore[arg-type]
    except ValueError:
        cents = None
    return PayoutItem(
        payout_item_id=raw.get("payout_item_id"),
        status=raw.get("transaction_status"),
        payout_batch_id=raw.get("payout_batch_id", payout_batch_id),
        recipient_type=detail.get("recipient_type"),
        receiver=detail.get("receiver"),
        amount_cents=cents,
        currency=amount.get("currency"),
        sender_item_id=detail.get("sender_item_id"),
    )


def _parse_batch(body: dict) -> PayoutBatch:
    header = body.get("batch_header")
    if not isinstance(header, dict) or not isinstance(header.get("payout_batch_id"), str):
        raise PayoutUnresolved("payout response has no batch_header.payout_batch_id")
    batch_id = header["payout_batch_id"]
    sender = header.get("sender_batch_header")
    raw_items = body.get("items")
    items = tuple(
        _parse_item(i, batch_id) for i in (raw_items if isinstance(raw_items, list) else [])
    )
    return PayoutBatch(
        payout_batch_id=batch_id,
        batch_status=header.get("batch_status"),
        sender_batch_id=sender.get("sender_batch_id") if isinstance(sender, dict) else None,
        items=items,
    )


def duplicate_batch_id(error_body: dict) -> str | None:
    """Existing payout_batch_id from a SENDER_BATCH_ID duplicate error, or None (SPIKE-11)."""
    if error_body.get("name") != "USER_BUSINESS_ERROR":
        return None
    found: set[str] = set()
    for detail in error_body.get("details") or []:
        if not isinstance(detail, dict) or detail.get("field") != "SENDER_BATCH_ID":
            continue
        for link in detail.get("link") or []:
            if not isinstance(link, dict) or link.get("rel") != "self":
                continue
            href = link.get("href")
            if isinstance(href, str) and (m := _BATCH_LINK.fullmatch(href)):
                found.add(m[1])
    return found.pop() if len(found) == 1 else None


class Payouts:
    def __init__(self, client: PayPalClient) -> None:
        self._client = client

    async def create_or_adopt(self, cmd: PayoutCommand) -> PayoutRef:
        """POST the batch once. On a duplicate sender_batch_id, adopt the batch iff it matches.

        Never retried here: each re-POST must first pass the halt and R20 rechecks (R19), which only
        settlement can run."""
        try:
            resp = await self._client.post(
                "/v1/payments/payouts",
                request_id=cmd.request_id,
                json=cmd.body(),
                execution_id=cmd.execution_id,
                retry=False,  # settlement retries, after halt + R20 rechecks (R19)
            )
        except PayPalError as err:
            if err.status == 400 and err.body.get("name") == "USER_BUSINESS_ERROR":
                existing = duplicate_batch_id(err.body)
                if existing is None:
                    raise PayoutUnresolved("duplicate batch error without a usable link") from err
                return await self._adopt(cmd, existing)
            raise
        batch = _parse_batch(resp.body)
        return PayoutRef(batch.payout_batch_id, None, adopted=False)

    async def _adopt(self, cmd: PayoutCommand, batch_id: str) -> PayoutRef:
        batch = await self.get_batch(batch_id, execution_id=cmd.execution_id)
        if not batch.matches(cmd):
            raise PayoutMismatch("existing batch does not match the frozen command", batch_id)
        return PayoutRef(batch_id, batch.items[0].payout_item_id, adopted=True)

    async def get_batch(
        self, payout_batch_id: str, *, execution_id: str | None = None
    ) -> PayoutBatch:
        resp = await self._client.get(
            f"/v1/payments/payouts/{_check_id(payout_batch_id)}", execution_id=execution_id
        )
        return _parse_batch(resp.body)

    async def get_item(self, payout_item_id: str, *, execution_id: str | None = None) -> PayoutItem:
        resp = await self._client.get(
            f"/v1/payments/payouts-item/{_check_id(payout_item_id)}", execution_id=execution_id
        )
        return _parse_item(resp.body, None)

    async def cancel_item(self, payout_item_id: str, *, execution_id: str) -> None:
        """Cancel an UNCLAIMED item. SPIKE-5: sandbox 500s; release only after a GET confirms."""
        await self._client.post(
            f"/v1/payments/payouts-item/{_check_id(payout_item_id)}/cancel",
            request_id=f"aegis-{execution_id}-cancel",
            execution_id=execution_id,
        )
