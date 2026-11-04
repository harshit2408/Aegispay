"""R3: no human decision or risk score can raise the three-way trusted cents ceiling."""

from hypothesis import given
from hypothesis import strategies as st

from tests.unit.test_policy_gate import gate, report


@given(
    billed=st.integers(0, 10**15),
    accepted=st.integers(0, 10**15),
    remaining=st.integers(0, 10**15),
    score=st.integers(0, 100),
    action=st.sampled_from(("APPROVE", "SHORT_PAY", "REJECT")),
)
def test_humans_never_raise_payable(billed, accepted, remaining, score, action):
    result = gate(
        match=report(
            billed_cents=billed, accepted_value_cents=accepted, po_remaining_cents=remaining
        ),
        score=score,
        votes=[("a", action), ("b", action)],
    )
    assert type(result.payable_cents) is int
    assert result.payable_cents == min(billed, accepted, remaining)


@given(st.integers(1, 10**12), st.integers(1, 10**12))
def test_hard_cap_and_po_exhaustion_cannot_be_voted_away(committed, payable):
    result = gate(
        match=report(
            billed_cents=payable, accepted_value_cents=payable, po_remaining_cents=payable
        ),
        committed=500000 + committed,
        votes=[("a", "APPROVE"), ("b", "APPROVE")],
    )
    assert result.route == "FREEZE" and result.required_approvals == 99
