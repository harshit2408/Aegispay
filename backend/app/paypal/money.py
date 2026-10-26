"""Integer cents <-> PayPal decimal strings (R4). Decimal exists only inside these two functions."""

import re
from decimal import Decimal

_AMOUNT = re.compile(r"[0-9]+(\.[0-9]{1,2})?")


def cents_to_str(cents: int) -> str:
    if type(cents) is not int:
        raise TypeError("cents must be int")
    if cents < 0:
        raise ValueError("cents must be >= 0")
    return f"{cents // 100}.{cents % 100:02d}"


def str_to_cents(value: str) -> int:
    """Parse a PayPal amount string. Rejects signs, exponents, sub-cent precision and whitespace."""
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise ValueError(f"not a USD amount with at most 2 decimals: {value!r}")
    return int(Decimal(value) * 100)
