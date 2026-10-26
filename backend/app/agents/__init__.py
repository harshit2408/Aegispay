"""Shared, process-local completion budget for the PRD §10 agents."""

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import get_ident
from uuid import UUID

from chronicle import current_session, default_hooks
from chronicle.capabilities.record.record_environment_types import RecordEnvironment
from chronicle.capabilities.record.recorder import record_request
from chronicle.framework.context.session_settings_types import SessionSettings

# TokenOps auto-loads dotenv at import; app.config alone owns secret configuration.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from tokenops import tokenops_run  # noqa: E402
from tokenops.capabilities import wrap_complete  # noqa: E402
from tokenops.capabilities.crossing import install_crossing_hook  # noqa: E402
from tokenops.capabilities.run import TokenOpsBound  # noqa: E402
from tokenops.framework import Halt  # noqa: E402
from tokenops.framework.core import Action, ActionKind, Observation, Usage  # noqa: E402
from tokenops.providers.types import ModelResponse  # noqa: E402

_LOG = logging.getLogger(__name__)
_PROVIDER = "openai"
_MODEL = "gpt-6.1-sol"
_OUTPUT_CAP = 1024
_GOVERNANCE = {
    "governance": {
        "budgets": [{"id": "invoice_llm_cap", "limit_micros": 1_000_000, "dimension": "trace"}],
        "policies": {
            "cost_budget": {"budget": "invoice_llm_cap"},
            "pre_call_worst_case": {"budget": "invoice_llm_cap", "default_max_output": _OUTPUT_CAP},
        },
    }
}


def _price(provider: str, model: str, usage: Usage) -> int:
    # Standard <=272k pricing reviewed in TOOLS.md; cached input gets no discount.
    if provider != _PROVIDER or model != _MODEL:
        raise ValueError("unreviewed provider or model price")
    return 2 * (usage.input + usage.cached) + 10 * (usage.output + usage.reasoning)


def _usage(response: ModelResponse) -> Usage:
    # Provider totals are inclusive: cached is part of input, reasoning part of output.
    return Usage(
        input=max(response.input_tokens - response.cached_tokens, 0),
        cached=response.cached_tokens,
        output=max(response.output_tokens - response.reasoning_tokens, 0),
        reasoning=response.reasoning_tokens,
    )


class InvoiceRun:
    """One synchronous invoice workflow. Pass complete into every component.

    The dispatch must make exactly one Standard provider request, with SDK retries
    disabled, enforce max_output_tokens, and return inclusive billable token totals.
    Only default Standard pricing is supported: no regional/priority premiums or cache writes.
    input_token_bound must include images, tool schemas and framing, from trusted code.
    """

    def __init__(self, execution_id: UUID, dispatch: Callable, bound: TokenOpsBound):
        self._execution_id = execution_id
        self._dispatch = dispatch
        self._bound = bound
        self._requests = 0
        self._active = True
        self._calling = False
        self._thread = get_ident()

    @property
    def spent_micros(self) -> int:
        return self._bound.governor.ledger.cost_micros(self._bound.attr.trace_id)

    def _halt(self, reason: str) -> None:
        trace_id = self._bound.attr.trace_id
        self._bound.governor.ledger.mark_halted(trace_id, reason)
        raise Halt(Action(kind=ActionKind.HALT, trace_id=trace_id, reason=reason)) from None

    def complete(self, node: str, messages: object, *, input_token_bound: int) -> ModelResponse:
        if not self._active:
            raise RuntimeError("invoice run is closed")
        if self._thread != get_ident() or self._calling:
            raise RuntimeError("invoice calls must run sequentially in their owning thread")
        session = current_session()
        if session is None or session.trace_id != self._bound.attr.trace_id:
            raise RuntimeError("invoice completion called from a different run scope")
        if node not in {"A2", "A3", "A4", "A5"}:
            raise ValueError("invoice completion node must be A2, A3, A4 or A5")
        if type(input_token_bound) is not int or not 0 < input_token_bound <= 272_000:
            raise ValueError("trusted input_token_bound must be an integer in 1..272000")
        if self._requests >= 4:
            self._halt("invoice limited to four provider requests")
        self._calling = True
        try:
            with tokenops_run(
                governor=self._bound.governor,
                controls=self._bound.controls,
                service=node,
                intent="invoice",
                provider=_PROVIDER,
                model=_MODEL,
                open_ledger_run=False,
                mode="enforce",
                headers={},
                payload={},
                user_dims={},
            ) as hop:

                def dispatch(provider, model, data, max_output_tokens=None):
                    self._requests += 1
                    try:
                        response = self._dispatch(
                            provider, model, data, max_output_tokens=max_output_tokens
                        )
                    except Exception:
                        self._halt("provider outcome uncertain; invoice halted")
                    except BaseException:
                        # Cancellation/shutdown: halt the run but keep the original signal.
                        self._bound.governor.ledger.mark_halted(
                            self._bound.attr.trace_id, "provider call interrupted"
                        )
                        raise
                    if not isinstance(response, ModelResponse):
                        self._halt("missing provider token usage")
                    counts = (
                        response.input_tokens,
                        response.output_tokens,
                        response.cached_tokens,
                        response.reasoning_tokens,
                    )
                    if (
                        any(type(n) is not int or n < 0 for n in counts)
                        or response.input_tokens == 0
                    ):
                        self._halt("invalid provider token usage")
                    rejected = (
                        response.input_tokens > input_token_bound
                        or response.output_tokens > _OUTPUT_CAP
                        or response.cached_tokens > response.input_tokens
                        or response.reasoning_tokens > response.output_tokens
                    )
                    usage = _usage(response)
                    record = {
                        "event": "llm_usage",
                        "execution_id": str(self._execution_id),
                        "node": node,
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cached_tokens": response.cached_tokens,
                        "reasoning_tokens": response.reasoning_tokens,
                        "cost_micros": _price(_PROVIDER, _MODEL, usage),
                    }
                    if rejected:
                        # The provider billed this request: log it and charge the budget
                        # before halting, so reconciliation (PRD §16) sees the overrun.
                        _LOG.info(json.dumps({**record, "rejected": True}))
                        self._bound.governor.ledger.record(
                            Observation(
                                attr=hop.attr,
                                node_type="llm",
                                boundary_id=f"{node}.chat",
                                ts=time.time(),
                                service=node,
                                provider=_PROVIDER,
                                model=_MODEL,
                                usage=usage,
                            )
                        )
                        self._halt("provider usage exceeded reviewed bounds")
                    _LOG.info(json.dumps(record))
                    return response

                governed = wrap_complete(
                    hop.governor,
                    hop.controls,
                    hop.attr,
                    provider=_PROVIDER,
                    model=_MODEL,
                    dispatch=dispatch,
                    service=node,
                    est_input_fn=lambda _: input_token_bound,
                    max_call_retries=0,
                )
                return governed(_PROVIDER, _MODEL, messages)
        finally:
            self._calling = False


@contextmanager
def invoice_run(execution_id: UUID, *, dispatch: Callable) -> Iterator[InvoiceRun]:
    """Tier 1: one run/trace, shared by all invoice agents, no trace body recording.

    shortcut: budget is process-local, add durable admission before graph restart/resume
    or multiple workers may execute the same invoice.
    """
    if not isinstance(execution_id, UUID):
        raise ValueError("execution_id must be a UUID")
    install_crossing_hook()
    environment = RecordEnvironment(
        service="aegispay.invoice",
        hooks=default_hooks,
        settings=SessionSettings(
            recording=False, capture_input=False, capture_output=False, fail_call_on_error=True
        ),
    )
    with (
        record_request(environment),
        tokenops_run(
            config=_GOVERNANCE,
            service="aegispay.invoice",
            intent="invoice",
            provider=_PROVIDER,
            model=_MODEL,
            price=_price,
            mode="enforce",
            headers={},
            payload={},
            user_dims={},
        ) as bound,
    ):
        run = InvoiceRun(execution_id, dispatch, bound)
        try:
            yield run
        finally:
            run._active = False
