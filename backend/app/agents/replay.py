"""DEMO_REPLAY_MODE completion seam (R24): recorded outputs, no provider client, no network."""

from collections.abc import Mapping, Sequence

from tokenops.providers.types import ModelResponse

from app.config import Settings


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
    # Settings already refuse replay without DEMO_ENABLED or with a non-sandbox PayPal URL.
    if not settings.demo_replay_mode:
        raise ValueError("replay requires DEMO_REPLAY_MODE")
    return ReplayComplete(recordings)
