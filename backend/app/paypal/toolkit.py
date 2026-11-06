"""Read-only tool registry for the A4 Investigator (PRD §9.5, R2, SPIKE-6 fallback).

Own read tools with the Agent Toolkit's names; this module never imports payouts.py.
"""

import inspect
import json
import re
from collections.abc import Callable, Iterable, Mapping

WRITE_NAME = re.compile(r"create|send|cancel|update|refund|capture|payout", re.I)
READ_NAME = re.compile(r"^(get|list|search|show)_")
WRITE_VERBS = frozenset(
    "create send cancel update refund capture payout payouts delete void remind post issue approve "
    "execute disburse transfer submit mark record pay reverse remove set add".split()
)
# Vendor-writable keys (incl. PayPal Invoicing names); strings under them are wrapped.
VENDOR_FIELDS = frozenset(
    "note memo terms terms_and_conditions description name reference business_name email_address "
    "additional_notes title comments comment full_name given_name surname".split()
)
OPEN, CLOSE = "<untrusted vendor_text>", "</untrusted>"


def assert_read_only(names: Iterable[str]) -> None:
    """R2 startup assertion: boot fails if any exposed tool name looks like a write."""
    bad = sorted(
        n
        for n in names
        if not READ_NAME.match(n) or WRITE_NAME.search(n) or WRITE_VERBS & set(n.lower().split("_"))
    )
    if bad:
        raise RuntimeError(f"write-capable tool names exposed to LLM: {bad}")


def wrap_untrusted(value: object, key: str = "") -> object:
    """Wrap vendor-writable free text so A4 treats it as data (R14)."""
    if isinstance(value, dict):
        return {k: wrap_untrusted(v, k if k in VENDOR_FIELDS else key) for k, v in value.items()}
    if isinstance(value, list):
        return [wrap_untrusted(v, key) for v in value]
    if isinstance(value, str) and key in VENDOR_FIELDS:
        return OPEN + value.replace("<", "&lt;") + CLOSE
    return value


def is_vendor_asserted(output: str, excerpt: str) -> bool:
    """True if the excerpt overlaps any wrapped vendor-text span in the tool output."""
    spans = [
        m.span() for m in re.finditer(re.escape(OPEN) + ".*?" + re.escape(CLOSE), output, re.S)
    ]
    for e in re.finditer(re.escape(excerpt), output):
        if any(e.start() < b and a < e.end() for a, b in spans):
            return True
    return False


class ReadTools:
    """Allowlisted registry. Validates names at construction (boot)."""

    def __init__(self, tools: Mapping[str, Callable[..., object]], scope: dict | None = None):
        assert_read_only(tools.keys())
        self._tools = dict(tools)
        self._scope = dict(scope or {})  # fixed args (e.g. invoice_id); model values never win

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def call(self, name: str, args: dict) -> str:
        """Run a tool; return wrapped JSON text. Unknown tool raises KeyError."""
        fn = self._tools[name]
        params = inspect.signature(fn).parameters
        unknown = set(args) - set(params)
        if unknown:
            raise ValueError(f"unknown args for {name}: {sorted(unknown)}")
        kwargs = {**args, **{k: v for k, v in self._scope.items() if k in params}}
        return json.dumps(wrap_untrusted(fn(**kwargs)), sort_keys=True, default=str)
