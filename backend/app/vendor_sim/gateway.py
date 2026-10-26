"""Vendor Simulator gateway (PRD §3, §9.2, R2, R7).

`VendorReader` is the only supplier interface the decision engine may hold: it can read one invoice
and nothing else. `VendorGateway` adds the supplier write calls and is constructed only where Vendor
write credentials exist (settlement, demo seeder); it is never reachable from an LLM (R2, R22).
"""

from datetime import date

from app.paypal.invoicing import Invoice, InvoiceSpec, Invoicing


class VendorReader:
    def __init__(self, invoicing: Invoicing) -> None:
        self._invoicing = invoicing

    async def get_invoice(self, invoice_id: str, *, execution_id: str | None = None) -> Invoice:
        return await self._invoicing.get(invoice_id, execution_id=execution_id)


class VendorGateway(VendorReader):
    async def create_draft(self, spec: InvoiceSpec, *, request_id: str) -> str:
        return await self._invoicing.create_draft(spec, request_id=request_id)

    async def send(self, invoice_id: str, *, request_id: str) -> None:
        await self._invoicing.send(invoice_id, request_id=request_id)

    async def record_payment(
        self,
        invoice_id: str,
        *,
        execution_id: str,
        amount_cents: int,
        payment_date: date,
        note: str,
    ) -> None:
        """R7: the only caller of record-payment; settlement guards it with the transition."""
        await self._invoicing.record_payment(
            invoice_id,
            execution_id=execution_id,
            amount_cents=amount_cents,
            payment_date=payment_date,
            note=note,
        )
