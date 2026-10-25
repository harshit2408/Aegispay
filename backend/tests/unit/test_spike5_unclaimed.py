import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import spike5_unclaimed  # noqa: E402


def test_cancel_uses_a_stable_request_id(monkeypatch):
    calls = []
    sandbox = SimpleNamespace(
        run="offline-run",
        token=lambda app: ("offline-token", []),
        payout=lambda *args: "offline-batch",
        poll_payout=lambda *args, **kwargs: ("offline-item", ("SUCCESS", "UNCLAIMED")),
        brief=lambda label, result: result,
    )

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 200, "offline-debug", {"transaction_status": "UNCLAIMED"}

    sandbox.call = call
    monkeypatch.setattr(spike5_unclaimed, "Sandbox", lambda: sandbox)

    assert spike5_unclaimed.main() == 0
    assert calls[0][0:2] == ("POST", "/v1/payments/payouts-item/offline-item/cancel")
    assert calls[0][2]["request_id"] == "aegis-spike-s5-offline-run-cancel"
