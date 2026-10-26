"""R4: integer cents; Decimal only at the PayPal string boundary."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.paypal.money import cents_to_str, str_to_cents


@pytest.mark.parametrize(
    ("cents", "text"), [(0, "0.00"), (5, "0.05"), (48000, "480.00"), (123456789, "1234567.89")]
)
def test_cents_to_str(cents, text):
    assert cents_to_str(cents) == text


@given(st.integers(min_value=0, max_value=10**12))
def test_round_trip(cents):
    assert str_to_cents(cents_to_str(cents)) == cents


@pytest.mark.parametrize(
    "bad", ["1.005", "1.0000", "-1.00", "abc", "", "1e2", "١.00", "NaN", "Infinity", " 1.00"]
)
def test_str_to_cents_rejects_sub_cent_and_garbage(bad):
    with pytest.raises(ValueError):
        str_to_cents(bad)


def test_str_to_cents_accepts_integers_and_one_decimal():
    assert str_to_cents("480") == 48000
    assert str_to_cents("4.8") == 480


@pytest.mark.parametrize("bad", [1.5, True, "5", -1, None, Decimal("1")])
def test_cents_to_str_rejects_non_int_or_negative(bad):
    with pytest.raises(
        TypeError if not isinstance(bad, int) or isinstance(bad, bool) else ValueError
    ):
        cents_to_str(bad)
