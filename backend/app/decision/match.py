"""P2-06: fresh trusted receiving/PO facts and vendor-asserted invoice evidence (R3/R9).

`match` returns a LangGraph state update and performs no writes. Its caller must FREEZE on
MatchUnavailable or any read failure; never reuse an earlier report after an exception. The graph
and authenticated upload route remain P2-10/P2-13. Indexed ingest must use the same normalization
and catch unique violations: this read does not replace R9's concurrent-insert protection.
"""

import re
import unicodedata
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.core import GoodsReceipt, MandatePolicy, PurchaseOrder, VendorMaster
from app.models.payables import InvoiceExecution, POLedger, SpendLedger
from app.schemas.decision import AegisState, InvoiceExtraction, MatchReport
from app.vendor_sim.gateway import Invoice, VendorReader

_INACTIVE = ("REJECTED", "DUPLICATE_BLOCKED")
# Common Latin-lookalike letters; exact comparison above never uses this risk-only mapping.
_CONFUSABLES = str.maketrans(
    {
        "0": "o",
        "1": "l",
        "а": "a",
        "ɑ": "a",
        "α": "a",
        "е": "e",
        "ε": "e",
        "о": "o",
        "ο": "o",
        "р": "p",
        "ρ": "p",
        "с": "c",
        "ϲ": "c",
        "х": "x",
        "χ": "x",
        "у": "y",
        "υ": "y",
        "і": "i",
        "ι": "i",
        "ј": "j",
        "ѕ": "s",
        "к": "k",
        "κ": "k",
        "м": "m",
        "μ": "m",
        "т": "t",
        "τ": "t",
        "в": "b",
        "ν": "v",
        "һ": "h",
    }
)


class MatchUnavailable(ValueError):
    """Missing/ambiguous trusted records. Caller must freeze the execution, never pay."""


def normalize_invoice_number(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[\s_/.\-]", "", value.upper())
    return re.sub(r"[0-9]+", lambda m: m[0].lstrip("0") or "0", compact) or None


def within_two_edits(left: str, right: str) -> bool:
    """Levenshtein <= 2 with a fixed-width band; linear work even for long input."""
    if abs(len(left) - len(right)) > 2:
        return False
    row = {j: j for j in range(min(len(right), 2) + 1)}
    for i, char in enumerate(left, 1):
        nxt = {}
        for j in range(max(0, i - 2), min(len(right), i + 2) + 1):
            nxt[j] = (
                min(
                    row.get(j, 3) + 1,
                    nxt.get(j - 1, 3) + 1,
                    row.get(j - 1, 3) + (j == 0 or char != right[j - 1]),
                )
                if j
                else i
            )
        if min(nxt.values()) > 2:
            return False
        row = nxt
    return row.get(len(right), 3) <= 2


def _email(value: object) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def build_match_report(
    *,
    extraction: InvoiceExtraction,
    invoice: Invoice | None,
    vendor: VendorMaster,
    po: PurchaseOrder,
    receipts: list[GoodsReceipt],
    po_committed_cents: int,
    vendor_invoices_today: int,
    vendor_committed_today_cents: int,
    now: datetime,
) -> MatchReport:
    """Pure match over current receipt leaves; no PDF field can set trusted value or payee."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if (
        not receipts
        or po.vendor_id != vendor.vendor_id
        or any(r.po_id != po.po_id for r in receipts)
    ):
        raise MatchUnavailable("NO_RECEIPT_OR_TRUSTED_PO_VENDOR")
    if po_committed_cents < 0 or vendor_committed_today_cents < 0:
        raise MatchUnavailable("INVALID_LEDGER_BALANCE")

    billed_qty, unit_cents, billed_cents = (
        extraction.billed_qty,
        extraction.unit_price_cents,
        extraction.total_cents,
    )
    number = extraction.vendor_invoice_number
    mismatches = []
    precision = True
    if invoice is None:
        mismatches.append("paypal_invoice")
    else:
        number = invoice.invoice_number
        item = invoice.items[0] if len(invoice.items) == 1 else None
        billed_qty = item.quantity if item and item.quantity is not None else 0
        unit_cents = item.unit_cents if item and item.unit_cents is not None else 0
        billed_cents = invoice.total_cents if invoice.total_cents is not None else 0
        precision = (
            not invoice.issues
            and invoice.currency == "USD"
            and item is not None
            and type(item.quantity) is int
            and item.quantity > 0
            and type(item.unit_cents) is int
            and item.unit_cents >= 0
            and type(invoice.total_cents) is int
            and invoice.total_cents >= 0
        )
        checks = {
            "invoice_number": normalize_invoice_number(number) is not None
            and normalize_invoice_number(number)
            == normalize_invoice_number(extraction.vendor_invoice_number),
            "billed_qty": billed_qty == extraction.billed_qty,
            "unit_price_cents": unit_cents == extraction.unit_price_cents,
            "total_cents": billed_cents == extraction.total_cents,
            "po_reference": invoice.reference == extraction.po_reference == po.po_id,
            "payee_email": bool(_email(invoice.invoicer_email))
            and _email(invoice.invoicer_email)
            == _email(extraction.payee_email)
            == _email(vendor.verified_paypal_email),
            "currency": invoice.currency == extraction.currency == po.currency == "USD",
            "precision": precision,
        }
        mismatches.extend(name for name, valid in checks.items() if not valid)
    if extraction.po_reference != po.po_id and "po_reference" not in mismatches:
        mismatches.append("po_reference")
    master_email, pdf_email = _email(vendor.verified_paypal_email), _email(extraction.payee_email)
    # A cooling/blocked master must fail G3 even though MatchReport has no trust-status field.
    payee_matches = (
        bool(pdf_email) and pdf_email == master_email and vendor.trust_status == "VERIFIED"
    )
    if not payee_matches and "payee_email" not in mismatches:
        mismatches.append("payee_email")
    # shortcut: common confusables only; use a reviewed Unicode table if the corpus requires more.
    lookalike = bool(pdf_email and master_email and pdf_email != master_email) and within_two_edits(
        unicodedata.normalize("NFKC", pdf_email).translate(_CONFUSABLES),
        unicodedata.normalize("NFKC", master_email).translate(_CONFUSABLES),
    )
    accepted = min(sum(r.accepted_qty for r in receipts), po.ordered_qty)
    accepted_value = accepted * po.unit_price_cents
    return MatchReport(
        po_id=po.po_id,
        po_version=po.version,
        grn_id=receipts[-1].grn_id,
        vendor_version=vendor.version,
        ordered_qty=po.ordered_qty,
        billed_qty=billed_qty,
        accepted_qty=accepted,
        po_unit_price_cents=po.unit_price_cents,
        billed_unit_price_cents=unit_cents,
        billed_cents=billed_cents,
        accepted_value_cents=accepted_value,
        po_remaining_cents=max(0, accepted_value - po_committed_cents)
        if po.status == "OPEN"
        else 0,
        payee_matches_vendor_master=payee_matches,
        payee_lookalike=lookalike,
        paypal_invoice_id=invoice.invoice_id if invoice else None,
        paypal_invoice_status=invoice.status if invoice else None,
        paypal_invoice_matches_pdf=invoice is not None and not mismatches,
        mismatched_fields=mismatches,
        precision_ok=precision,
        vendor_age_days=max(0, (now - vendor.onboarded_at).days),
        vendor_invoices_today=vendor_invoices_today,
        vendor_committed_today_cents=vendor_committed_today_cents,
        invoice_number_norm=normalize_invoice_number(number),
        duplicate_of=None,
        fuzzy_duplicate_of=None,
    )


async def _fresh(session: AsyncSession, model, condition):
    return (
        await session.execute(
            select(model).where(condition).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _duplicates(
    session: AsyncSession, execution: InvoiceExecution, report: MatchReport, now: datetime
):
    live = (
        InvoiceExecution.execution_id != execution.execution_id,
        InvoiceExecution.status.not_in(_INACTIVE),
    )
    exact_keys = []
    if report.invoice_number_norm:
        exact_keys.append(
            (InvoiceExecution.vendor_id == execution.vendor_id)
            & (InvoiceExecution.invoice_number_norm == report.invoice_number_norm)
        )
    if report.paypal_invoice_id:
        exact_keys.append(InvoiceExecution.paypal_invoice_id == report.paypal_invoice_id)
    if exact_keys:
        exact = (
            await session.execute(
                select(InvoiceExecution.execution_id)
                .where(*live, or_(*exact_keys))
                .order_by(InvoiceExecution.created_at, InvoiceExecution.execution_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if exact:
            report.duplicate_of = exact
            return
    if not report.invoice_number_norm:
        return
    query = (
        select(InvoiceExecution.execution_id, InvoiceExecution.invoice_number_norm)
        .where(
            *live,
            InvoiceExecution.vendor_id == execution.vendor_id,
            InvoiceExecution.po_id == execution.po_id,
            InvoiceExecution.created_at >= now - timedelta(days=30),
            InvoiceExecution.created_at <= now,
            InvoiceExecution.invoice_number_norm.is_not(None),
            func.abs(InvoiceExecution.billed_cents - report.billed_cents) * 10
            <= InvoiceExecution.billed_cents,
        )
        .order_by(InvoiceExecution.created_at, InvoiceExecution.execution_id)
    )
    for old_id, number in await session.execute(query):
        if within_two_edits(report.invoice_number_norm, number):
            report.fuzzy_duplicate_of = old_id
            break


async def match(
    state: AegisState, *, session: AsyncSession, reader: VendorReader, now: datetime | None = None
) -> AegisState:
    """Read trusted execution bindings; ignore state-supplied PO/vendor IDs and stale report.

    Caller supplies a clean READ COMMITTED session without row locks across the invoice GET.
    FREEZE on any exception. This node cannot approve, reserve, set a payee or update money state.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if session.new or session.dirty or session.deleted:
        raise MatchUnavailable("MATCH_REQUIRES_CLEAN_SESSION")
    connection = await session.connection()
    if await connection.get_isolation_level() != "READ COMMITTED":
        raise MatchUnavailable("MATCH_REQUIRES_READ_COMMITTED")
    raw = state.get("extraction")
    try:
        extraction = InvoiceExtraction.model_validate(
            raw.model_dump() if isinstance(raw, InvoiceExtraction) else raw, strict=True
        )
    except ValidationError as exc:
        raise MatchUnavailable("INVALID_EXTRACTION") from exc
    with session.no_autoflush:
        execution = await _fresh(
            session, InvoiceExecution, InvoiceExecution.execution_id == state.get("execution_id")
        )
        if execution is None:
            raise MatchUnavailable("NO_EXECUTION")
        invoice_id = execution.paypal_invoice_id
        invoice = (
            await reader.get_invoice(invoice_id, execution_id=execution.execution_id)
            if invoice_id
            else None
        )
        execution = await _fresh(
            session, InvoiceExecution, InvoiceExecution.execution_id == execution.execution_id
        )
        if execution is None or execution.paypal_invoice_id != invoice_id:
            raise MatchUnavailable("SOURCE_BINDING_CHANGED")
        if invoice is not None and invoice.invoice_id != invoice_id:
            raise MatchUnavailable("SOURCE_ID_MISMATCH")
        po = await _fresh(session, PurchaseOrder, PurchaseOrder.po_id == execution.po_id)
        vendor = await _fresh(session, VendorMaster, VendorMaster.vendor_id == execution.vendor_id)
        policy = await _fresh(
            session,
            MandatePolicy,
            (MandatePolicy.policy_id == execution.policy_id)
            & (MandatePolicy.version == execution.policy_version),
        )
        if po is None or vendor is None or policy is None:
            raise MatchUnavailable("NO_TRUSTED_PO_VENDOR_POLICY")
        correction = aliased(GoodsReceipt)
        receipts = list(
            (
                await session.execute(
                    select(GoodsReceipt)
                    .where(
                        GoodsReceipt.po_id == po.po_id,
                        ~select(correction.grn_id)
                        .where(correction.supersedes_grn_id == GoodsReceipt.grn_id)
                        .exists(),
                    )
                    .order_by(GoodsReceipt.created_at, GoodsReceipt.grn_id)
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        signed_po = case(
            (POLedger.kind == "RESERVE", POLedger.amount_cents), else_=-POLedger.amount_cents
        )
        # Exclude this execution's own reserve when a review/resume re-evaluates its payable.
        po_committed = (
            await session.execute(
                select(func.coalesce(func.sum(signed_po), 0)).where(
                    POLedger.po_id == po.po_id,
                    POLedger.execution_id != execution.execution_id,
                )
            )
        ).scalar_one()
        zone = ZoneInfo(policy.cap_timezone)
        day = now.astimezone(zone).date()
        start = datetime.combine(day, time.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        count = (
            await session.execute(
                select(func.count())
                .select_from(InvoiceExecution)
                .where(
                    InvoiceExecution.vendor_id == vendor.vendor_id,
                    InvoiceExecution.policy_id == policy.policy_id,
                    InvoiceExecution.status.not_in(_INACTIVE),
                    InvoiceExecution.replayed == execution.replayed,
                    InvoiceExecution.created_at >= start,
                    InvoiceExecution.created_at < end,
                )
            )
        ).scalar_one()
        signed_spend = case(
            (SpendLedger.kind == "RESERVE", SpendLedger.amount_cents),
            else_=-SpendLedger.amount_cents,
        )
        committed = (
            await session.execute(
                select(func.coalesce(func.sum(signed_spend), 0)).where(
                    SpendLedger.vendor_id == vendor.vendor_id,
                    SpendLedger.policy_id == policy.policy_id,
                    SpendLedger.business_date == day,
                    SpendLedger.execution_id != execution.execution_id,
                )
            )
        ).scalar_one()
        report = build_match_report(
            extraction=extraction,
            invoice=invoice,
            vendor=vendor,
            po=po,
            receipts=receipts,
            po_committed_cents=int(po_committed),
            vendor_invoices_today=count,
            vendor_committed_today_cents=int(committed),
            now=now,
        )
        await _duplicates(session, execution, report, now)
    return {"match": report}
