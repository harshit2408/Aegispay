"""Read-only tool registry for the A4 Investigator (PRD §9.5, R2, SPIKE-6 fallback).

Own read tools with the Agent Toolkit's names; this module never imports payouts.py.
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping

WRITE_NAME = re.compile(r"create|send|cancel|update|refund|capture|payout", re.I)
VENDOR_FIELDS = frozenset({"note", "memo", "terms", "description", "name", "reference"})
OPEN, CLOSE = "<untrusted vendor_text>", "</untrusted>"


def assert_read_only(names: Iterable[str]) -> None:
    """R2 startup assertion: boot fails if any exposed tool name looks like a write."""
    bad = sorted(n for n in names if WRITE_NAME.search(n))
    if bad:
        raise RuntimeError(f"write-capable tool names exposed to LLM: {bad}")


def wrap_untrusted(value: object, key: str = "") -> object:
    """Wrap vendor-writable free text so A4 treats it as data (R14)."""
    if isinstance(value, dict):
        return {k: wrap_untrusted(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [wrap_untrusted(v, key) for v in value]
    if isinstance(value, str) and key in VENDOR_FIELDS:
        return OPEN + value.replace(CLOSE, "").replace(OPEN, "") + CLOSE
    return value


def is_vendor_asserted(output: str, excerpt: str) -> bool:
    """True if the excerpt overlaps any wrapped vendor-text span in the tool output."""
    for m in re.finditer(re.escape(OPEN) + ".*?" + re.escape(CLOSE), output, re.S):
        span = m.group(0)
        if excerpt in span or span in excerpt:
            return True
    return False


class ReadTools:
    """Allowlisted registry. Validates names at construction (boot)."""

    def __init__(self, tools: Mapping[str, Callable[..., object]]):
        assert_read_only(tools.keys())
        self._tools = dict(tools)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def call(self, name: str, args: dict) -> str:
        """Run a tool; return wrapped JSON text. Unknown tool raises KeyError."""
        return json.dumps(wrap_untrusted(self._tools[name](**args)), sort_keys=True, default=str)
