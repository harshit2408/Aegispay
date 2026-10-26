"""P1-07: outbox worker leases, SKIP LOCKED claims, halt, attempt cap (PRD §11.4, R19, R23)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models.payables import InvoiceExecution, SettlementControl, SettlementOutbox
from app.settlement.outbox_worker import LEASE, MAX_ATTEMPTS, claim_next, tick
from tests.seed import seed_approved, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


class FakeService:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def execute(self, execution_id):
        self.calls.append(execution_id)
        if self.fail:
            raise RuntimeError("boom")
        return "PAYOUT_SUBMITTED"


@pytest.fixture
async def sm(pg_sessionmaker):
    async with pg_sessionmaker() as s, s.begin():
        await seed_world(s)
        for i in range(3):
            await seed_approved(s, f"e{i}", paypal_invoice_id=f"INV{i}")
    return pg_sessionmaker


async def outbox(sm, execution_id):
    async with sm() as s:
        query = select(SettlementOutbox).where(SettlementOutbox.execution_id == execution_id)
        return (await s.execute(query)).scalar_one()


async def test_claim_is_fifo_leased_and_counts_attempts(sm):
    async with sm() as s, s.begin():
        row = await claim_next(s, NOW)
        assert row.execution_id == "e0"
    claimed = await outbox(sm, "e0")
    assert (claimed.status, claimed.attempts) == ("CLAIMED", 1)
    assert claimed.claimed_until == NOW + LEASE


async def test_two_concurrent_claims_never_get_the_same_row(sm):
    async with sm() as a, a.begin(), sm() as b, b.begin():
        first = await claim_next(a, NOW)
        second = await claim_next(b, NOW)  # SKIP LOCKED: e0 is locked by the open tx
        assert {first.execution_id, second.execution_id} == {"e0", "e1"}


async def test_unexpired_lease_is_not_reclaimed_but_expired_one_is(sm):
    for _ in range(3):
        async with sm() as s, s.begin():
            await claim_next(s, NOW)
    async with sm() as s, s.begin():
        assert await claim_next(s, NOW + timedelta(seconds=59)) is None
    async with sm() as s, s.begin():
        row = await claim_next(s, NOW + LEASE + timedelta(seconds=1))
        assert row.execution_id == "e0" and row.attempts == 2


async def test_gives_up_after_max_attempts(sm):
    async with sm() as s, s.begin():
        await s.execute(
            update(SettlementOutbox)
            .where(SettlementOutbox.execution_id == "e0")
            .values(attempts=MAX_ATTEMPTS, status="CLAIMED", claimed_until=NOW)
        )
    async with sm() as s, s.begin():
        nxt = await claim_next(s, NOW + timedelta(seconds=1))
        assert nxt.execution_id == "e1"  # the exhausted command is skipped, not retried
    row = await outbox(sm, "e0")
    assert (row.status, row.last_error) == ("FAILED", "MAX_ATTEMPTS")


async def test_tick_executes_the_claimed_command(sm):
    service = FakeService()
    assert await tick(sm, service, NOW) is True
    assert service.calls == ["e0"]


async def test_tick_returns_false_when_nothing_is_ready(sm):
    async with sm() as s, s.begin():
        await s.execute(update(SettlementOutbox).values(status="DONE"))
    assert await tick(sm, FakeService(), NOW) is False


async def test_tick_does_nothing_while_halted(sm):
    async with sm() as s, s.begin():
        await s.execute(update(SettlementControl).values(halted=True))
    service = FakeService()
    assert await tick(sm, service, NOW) is False
    assert service.calls == []
    assert (await outbox(sm, "e0")).status == "PENDING"


async def test_failure_keeps_the_claim_for_lease_expiry_retry(sm):
    service = FakeService(fail=True)
    assert await tick(sm, service, NOW) is True
    row = await outbox(sm, "e0")
    assert (row.status, row.last_error, row.attempts) == ("CLAIMED", "RuntimeError", 1)


async def test_give_up_on_a_stranded_payout_sending_goes_to_manual_review(sm):
    async with sm() as s, s.begin():
        await s.execute(
            update(InvoiceExecution)
            .where(InvoiceExecution.execution_id == "e0")
            .values(status="PAYOUT_SENDING")
        )
        await s.execute(
            update(SettlementOutbox)
            .where(SettlementOutbox.execution_id == "e0")
            .values(attempts=MAX_ATTEMPTS, status="CLAIMED", claimed_until=NOW)
        )
    async with sm() as s, s.begin():
        await claim_next(s, NOW + timedelta(seconds=1))
    async with sm() as s:
        assert (await s.get(InvoiceExecution, "e0")).status == "NEEDS_MANUAL_REVIEW"
