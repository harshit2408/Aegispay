"""Atomic PO consumption (R17). Caller owns the transaction; the PO lock serializes reserves."""

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.core import GoodsReceipt, PurchaseOrder
from app.models.payables import InvoiceExecution, POLedger


class OverConsumption(Exception):
    """The command exceeds the PO's remaining accepted value."""


async def accepted_value_cents(session: AsyncSession, po: PurchaseOrder) -> int:
    correction = aliased(GoodsReceipt)
    qty = select(func.coalesce(func.sum(GoodsReceipt.accepted_qty), 0)).where(
        GoodsReceipt.po_id == po.po_id,
        ~select(correction.grn_id)
        .where(correction.supersedes_grn_id == GoodsReceipt.grn_id)
        .exists(),
    )
    accepted = int((await session.execute(qty)).scalar_one())
    return min(accepted, po.ordered_qty) * po.unit_price_cents


async def committed_cents(session: AsyncSession, po_id: str) -> int:
    signed = case((POLedger.kind == "RESERVE", POLedger.amount_cents), else_=-POLedger.amount_cents)
    query = select(func.coalesce(func.sum(signed), 0)).where(POLedger.po_id == po_id)
    return int((await session.execute(query)).scalar_one())


async def open_reserve(session: AsyncSession, execution_id: str) -> POLedger | None:
    released = aliased(POLedger)
    query = select(POLedger).where(
        POLedger.execution_id == execution_id,
        POLedger.kind == "RESERVE",
        ~select(released.entry_id).where(released.reserve_entry_id == POLedger.entry_id).exists(),
    )
    return (await session.execute(query)).scalar_one_or_none()


async def reserve(
    session: AsyncSession, *, execution_id: str, po_id: str, amount_cents: int
) -> int:
    if type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive int")
    po = (
        await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.po_id == po_id).with_for_update()
        )
    ).scalar_one()
    existing = await open_reserve(session, execution_id)
    if existing is not None:
        if existing.po_id != po_id or existing.amount_cents != amount_cents:
            raise ValueError("open PO reservation differs from the command")
        return existing.entry_id
    if po.status != "OPEN" or (
        await committed_cents(session, po_id) + amount_cents
        > await accepted_value_cents(session, po)
    ):
        raise OverConsumption(po_id)
    entry = POLedger(
        po_id=po_id,
        execution_id=execution_id,
        kind="RESERVE",
        qty=(amount_cents + po.unit_price_cents - 1) // po.unit_price_cents,
        amount_cents=amount_cents,
    )
    session.add(entry)
    await session.flush()
    return entry.entry_id


async def release(session: AsyncSession, *, reserve_entry_id: int) -> int:
    """Release only alongside the spend release, after confirmed non-payment. Idempotent."""
    reserved = await session.get(POLedger, reserve_entry_id)
    if reserved is None or reserved.kind != "RESERVE":
        raise ValueError(f"{reserve_entry_id} is not a PO RESERVE entry")
    inserted = (
        await session.execute(
            pg_insert(POLedger)
            .values(
                po_id=reserved.po_id,
                execution_id=reserved.execution_id,
                kind="RELEASE",
                qty=reserved.qty,
                amount_cents=reserved.amount_cents,
                reserve_entry_id=reserve_entry_id,
            )
            .on_conflict_do_nothing(constraint="uq_poled_one_release_per_reserve")
            .returning(POLedger.entry_id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    query = select(POLedger.entry_id).where(POLedger.reserve_entry_id == reserve_entry_id)
    return (await session.execute(query)).scalar_one()


async def close_if_fully_paid(session: AsyncSession, po_id: str) -> None:
    po = (
        await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.po_id == po_id).with_for_update()
        )
    ).scalar_one()
    released = aliased(POLedger)
    paid = (
        select(func.coalesce(func.sum(POLedger.amount_cents), 0))
        .join(InvoiceExecution, InvoiceExecution.execution_id == POLedger.execution_id)
        .where(
            POLedger.po_id == po_id,
            POLedger.kind == "RESERVE",
            InvoiceExecution.status.in_(("PAYOUT_SUCCEEDED", "SETTLED")),
            ~select(released.entry_id)
            .where(released.reserve_entry_id == POLedger.entry_id)
            .exists(),
        )
    )
    accepted = await accepted_value_cents(session, po)
    if accepted > 0 and (await session.execute(paid)).scalar_one() >= accepted:
        po.status = "CLOSED"
