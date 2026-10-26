"""Execution status transitions (PRD §8 allowed pairs, R6).

Every status change goes through `apply`: the pair must be in ALLOWED and the caller must hold
the row lock (`SELECT … FOR UPDATE`); `row_version` is bumped and optionally checked against
what the caller read.
"""

import logging

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payables import InvoiceExecution

log = logging.getLogger("aegis.settlement")

ALLOWED: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"ANALYZING"}),
    "ANALYZING": frozenset({"FROZEN", "DUPLICATE_BLOCKED", "APPROVED_FOR_PAYMENT"}),
    "FROZEN": frozenset({"APPROVED_FOR_PAYMENT", "REJECTED", "FROZEN"}),
    "APPROVED_FOR_PAYMENT": frozenset({"PAYOUT_SENDING", "FROZEN"}),
    "PAYOUT_SENDING": frozenset({"PAYOUT_SUBMITTED", "PAYOUT_FAILED", "NEEDS_MANUAL_REVIEW"}),
    "PAYOUT_SUBMITTED": frozenset(
        {"PAYOUT_SUCCEEDED", "PAYOUT_FAILED", "FROZEN", "NEEDS_MANUAL_REVIEW"}
    ),
    "PAYOUT_SUCCEEDED": frozenset({"SETTLED", "NEEDS_MANUAL_REVIEW"}),
    "SETTLED": frozenset({"NEEDS_MANUAL_REVIEW"}),
}


class IllegalTransition(Exception):
    def __init__(self, execution_id: str, current: str, target: str) -> None:
        super().__init__(f"{execution_id}: {current} -> {target} is not allowed")
        self.execution_id, self.current, self.target = execution_id, current, target


class StaleVersion(Exception):
    """The row changed since the caller read it (optimistic lock, R6)."""


async def apply(
    session: AsyncSession,
    execution: InvoiceExecution,
    to: str,
    *,
    expected_version: int | None = None,
) -> None:
    if expected_version is not None and execution.row_version != expected_version:
        raise StaleVersion(execution.execution_id)
    if to not in ALLOWED.get(execution.status, frozenset()):
        log.warning(
            "illegal transition %s -> %s execution_id=%s",
            execution.status,
            to,
            execution.execution_id,
        )
        raise IllegalTransition(execution.execution_id, execution.status, to)
    execution.status = to
    execution.row_version += 1
    execution.updated_at = func.now()
    await session.flush()


async def apply_or_ignore(session: AsyncSession, execution: InvoiceExecution, to: str) -> bool:
    """For late or duplicate PayPal signals: an illegal or backward move is dropped (R6)."""
    try:
        await apply(session, execution, to)
    except IllegalTransition:
        return False
    return True
