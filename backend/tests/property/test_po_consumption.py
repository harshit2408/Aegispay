"""Generated reserve/release sequences prove the R17 cents invariant on PostgreSQL."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.settlement import po_ledger
from tests.seed import make_execution, seed_approved, seed_world

pytestmark = pytest.mark.usefixtures("pg_template")


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(st.tuples(st.integers(1, 60000), st.booleans()), min_size=1, max_size=15))
async def test_reserved_cents_never_exceed_accepted_value(pg_sessionmaker, operations):
    async with pg_sessionmaker() as s:
        async with s.begin():
            await seed_world(s)
            await seed_approved(s, "e0")
            expected = 0
            for i, (amount, release_now) in enumerate(operations):
                if i:
                    s.add(make_execution(f"e{i}"))
                    await s.flush()
                if expected + amount > 48000:
                    with pytest.raises(po_ledger.OverConsumption):
                        await po_ledger.reserve(
                            s, execution_id=f"e{i}", po_id="PO-1042", amount_cents=amount
                        )
                else:
                    entry = await po_ledger.reserve(
                        s, execution_id=f"e{i}", po_id="PO-1042", amount_cents=amount
                    )
                    expected += amount
                    if release_now:
                        await po_ledger.release(s, reserve_entry_id=entry)
                        await po_ledger.release(s, reserve_entry_id=entry)
                        expected -= amount
                assert await po_ledger.committed_cents(s, "PO-1042") == expected
                assert 0 <= expected <= 48000
            await s.rollback()  # each generated example starts from an empty database
