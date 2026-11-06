"""DEMO_REPLAY_MODE completion seam (R24): recorded outputs, no provider client, no network."""

from collections.abc import Mapping, Sequence

from tokenops.providers.types import ModelResponse

from app.config import Settings

SANDBOX_URLS = ("https://api-m.sandbox.paypal.com", "https://api.sandbox.paypal.com")


class ReplayComplete:
    """Drop-in for InvoiceRun.complete. Consumes recordings per node, in order."""

    replayed = True  # callers must stamp the execution replayed=true (R24)

    def __init__(self, recordings: Mapping[str, Sequence[str]]):
        self._queues = {node: list(items) for node, items in recordings.items()}
        self.served: list[str] = []

    def __call__(self, node: str, messages: object, *, input_token_bound: int) -> ModelResponse:
        queue = self._queues.get(node)
        if not queue:
            raise LookupError(f"no recorded response for {node}")  # fail closed
        self.served.append(node)
        return ModelResponse(content=queue.pop(0))


def replay_complete(settings: Settings, recordings: Mapping[str, Sequence[str]]) -> ReplayComplete:
    # Settings validate this too, but model_copy()/model_construct() skip validators,
    # so the seam re-checks R24 itself.
    if not settings.demo_replay_mode:
        raise ValueError("replay requires DEMO_REPLAY_MODE")
    if not settings.demo_enabled:
        raise ValueError("replay requires DEMO_ENABLED")
    if settings.paypal_base_url not in SANDBOX_URLS:
        raise ValueError("replay requires sandbox PayPal")
    return ReplayComplete(recordings)
