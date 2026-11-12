"""PRD §12.4 short-pay notice: draft with A5 after approval, record one vendor response.

Facts come only from locked DB rows on the whitelist (R14): the withheld delta is
`billed_cents - approved_cents` of the already-frozen approval, so a notice can never change
what is paid (R3). Visibility to the supplier starts when settlement sets NOTICE_SENT (§11.4).
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.agents.notice import Notice, NoticeFacts, draft_notice, respond_to_notice
from app.agents.replay import ReplayComplete
from app.audit.events import enqueue_event
from app.models.core import GoodsReceipt
from app.models.payables import InvoiceExecution, ShortPayNotice

log = logging.getLogger(__name__)
Drafter = Callable[[InvoiceExecution, NoticeFacts], Notice]


class NoticeConflict(ValueError):
    """The notice is not open for a response (not sent yet, or already answered)."""


def _unavailable(node, messages, *, input_token_bound):
    raise LookupError("A5 provider unavailable")


def governed_drafter(settings) -> Drafter:
    """A5 inside the execution's durable invoice budget; replay never calls a provider (R24)."""
    from app.agents.provider import governed_analysis
    from app.decision.analyze import _budget_id

    def draft(execution: InvoiceExecution, facts: NoticeFacts) -> Notice:
        if execution.replayed:
            return draft_notice(ReplayComplete({}), facts)  # no A5 recording → fallback template
        try:
            return governed_analysis(
                _budget_id(execution.execution_id),
                settings=settings,
                action=lambda complete: draft_notice(complete, facts),
            )
        except Exception as exc:  # provider not configured or budget refused: static template
            log.warning(
                "A5 unavailable execution_id=%s error=%s",
                execution.execution_id,
                type(exc).__name__,
            )
            return draft_notice(_unavailable, facts)

    return draft


def _facts(execution: InvoiceExecution, grn: GoodsReceipt) -> NoticeFacts:
    fields = {
        "po_number": execution.po_id,
        "delta_cents": execution.billed_cents - execution.approved_cents,
        "accepted_qty": grn.accepted_qty,
        "rejected_qty": grn.rejected_qty,
        "inspection_note": grn.inspection_note or "",
    }
    try:
        return NoticeFacts(invoice_number=execution.vendor_invoice_number or "", **fields)
    except ValidationError:  # vendor-asserted number outside the identifier whitelist
        return NoticeFacts(invoice_number=execution.invoice_number_norm, **fields)


async def create_notice(sessions, draft: Drafter, execution_id: str) -> str | None:
    """Idempotent on execution_id: a re-run returns the stored notice and calls no LLM."""
    async with sessions() as session:
        existing = await session.scalar(
            select(ShortPayNotice.notice_id).where(ShortPayNotice.execution_id == execution_id)
        )
        if existing is not None:
            return existing
        execution = await session.get(InvoiceExecution, execution_id)
        if (
            execution is None
            or execution.route != "SHORT_PAY"
            or execution.approved_cents is None
            or execution.billed_cents is None
            or execution.billed_cents <= execution.approved_cents
            or execution.grn_id is None
        ):
            return None
        grn = await session.get(GoodsReceipt, execution.grn_id)
        facts = _facts(execution, grn)
    notice = await asyncio.to_thread(draft, execution, facts)
    notice_id = f"NTC-{execution_id}"
    async with sessions() as session, session.begin():
        inserted = await session.scalar(
            insert(ShortPayNotice)
            .values(
                notice_id=notice_id,
                execution_id=execution_id,
                text=notice.text,
                delta_cents=facts.delta_cents,
                used_fallback=notice.used_fallback,
            )
            .on_conflict_do_nothing(index_elements=["execution_id"])
            .returning(ShortPayNotice.notice_id)
        )
        if inserted is not None:
            await enqueue_event(
                session,
                execution_id=execution_id,
                actor="system:decision",
                event_type="SHORT_PAY_NOTICE_DRAFTED",
                payload={
                    "notice_id": notice_id,
                    "delta_cents": facts.delta_cents,
                    "used_fallback": notice.used_fallback,
                },
            )
    return notice_id


async def respond(session, notice_id: str, action: str, reason: str | None, actor: str) -> dict:
    """One response per notice. Locks the execution row; the payout amount is never touched."""
    notice = await session.get(
        ShortPayNotice, notice_id, with_for_update=True, populate_existing=True
    )
    if notice is None:
        raise LookupError("notice not found")
    execution = await session.get(
        InvoiceExecution, notice.execution_id, with_for_update=True, populate_existing=True
    )
    if notice.response is not None:
        raise NoticeConflict("notice already answered")
    try:
        outcome = respond_to_notice(execution.short_pay_balance, action)
    except ValueError:
        raise NoticeConflict("notice is not open") from None
    now = datetime.now(UTC)
    notice.response, notice.reason = outcome, reason
    notice.responded_by, notice.responded_at = actor, now
    execution.short_pay_balance = outcome
    await enqueue_event(
        session,
        execution_id=execution.execution_id,
        actor=actor,
        event_type=f"SHORT_PAY_NOTICE_{outcome}",
        payload={"notice_id": notice_id, "delta_cents": notice.delta_cents},
        ts=now,
    )
    return {"notice_id": notice_id, "short_pay_balance": outcome, "responded_at": now}
