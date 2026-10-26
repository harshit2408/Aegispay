"""P1-07: R6 allowed-pairs table (PRD §8) and optimistic row_version."""

import pytest

from app.models.payables import EXECUTION_STATUSES, InvoiceExecution
from app.settlement.transitions import (
    ALLOWED,
    IllegalTransition,
    StaleVersion,
    apply,
    apply_or_ignore,
)

PRD_PAIRS = {
    ("RECEIVED", "ANALYZING"),
    ("ANALYZING", "FROZEN"),
    ("ANALYZING", "DUPLICATE_BLOCKED"),
    ("ANALYZING", "APPROVED_FOR_PAYMENT"),
    ("FROZEN", "APPROVED_FOR_PAYMENT"),
    ("FROZEN", "REJECTED"),
    ("FROZEN", "FROZEN"),
    ("APPROVED_FOR_PAYMENT", "PAYOUT_SENDING"),
    ("APPROVED_FOR_PAYMENT", "FROZEN"),
    ("PAYOUT_SENDING", "PAYOUT_SUBMITTED"),
    ("PAYOUT_SENDING", "PAYOUT_FAILED"),
    ("PAYOUT_SENDING", "NEEDS_MANUAL_REVIEW"),
    ("PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED"),
    ("PAYOUT_SUBMITTED", "PAYOUT_FAILED"),
    ("PAYOUT_SUBMITTED", "FROZEN"),
    ("PAYOUT_SUBMITTED", "NEEDS_MANUAL_REVIEW"),
    ("PAYOUT_SUCCEEDED", "SETTLED"),
    ("PAYOUT_SUCCEEDED", "NEEDS_MANUAL_REVIEW"),
    ("SETTLED", "NEEDS_MANUAL_REVIEW"),
}


class FakeSession:
    flushes = 0

    async def flush(self):
        self.flushes += 1


def execution(status, version=0):
    return InvoiceExecution(execution_id="e1", status=status, row_version=version)


def test_table_is_exactly_the_prd_pairs():
    assert {(a, b) for a, targets in ALLOWED.items() for b in targets} == PRD_PAIRS


def test_every_status_in_the_table_is_a_known_status():
    known = set(EXECUTION_STATUSES)
    assert set(ALLOWED) <= known and all(t <= known for t in ALLOWED.values())


@pytest.mark.parametrize(("current", "target"), sorted(PRD_PAIRS))
async def test_allowed_pair_applies_and_bumps_version(current, target):
    ex = execution(current, version=4)
    await apply(FakeSession(), ex, target)
    assert (ex.status, ex.row_version) == (target, 5)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("PAYOUT_SUCCEEDED", "PAYOUT_SUBMITTED"),
        ("SETTLED", "PAYOUT_SUCCEEDED"),
        ("SETTLED", "SETTLED"),
        ("REJECTED", "APPROVED_FOR_PAYMENT"),
        ("FROZEN", "PAYOUT_SENDING"),
        ("RECEIVED", "PAYOUT_SUBMITTED"),
        ("PAYOUT_FAILED", "PAYOUT_SUBMITTED"),
        ("NEEDS_MANUAL_REVIEW", "SETTLED"),
    ],
)
async def test_illegal_or_backward_pair_is_rejected_and_leaves_row_untouched(current, target):
    ex = execution(current, version=2)
    with pytest.raises(IllegalTransition):
        await apply(FakeSession(), ex, target)
    assert (ex.status, ex.row_version) == (current, 2)


async def test_stale_expected_version_is_rejected():
    ex = execution("APPROVED_FOR_PAYMENT", version=3)
    with pytest.raises(StaleVersion):
        await apply(FakeSession(), ex, "PAYOUT_SENDING", expected_version=2)
    await apply(FakeSession(), ex, "PAYOUT_SENDING", expected_version=3)
    assert ex.status == "PAYOUT_SENDING"


async def test_apply_or_ignore_drops_a_late_pending_after_success():
    ex = execution("PAYOUT_SUCCEEDED")
    assert await apply_or_ignore(FakeSession(), ex, "PAYOUT_SUBMITTED") is False
    assert ex.status == "PAYOUT_SUCCEEDED"
    assert await apply_or_ignore(FakeSession(), ex, "SETTLED") is True
