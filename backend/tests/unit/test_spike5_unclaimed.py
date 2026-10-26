import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import common  # noqa: E402
import spike5_unclaimed  # noqa: E402


def test_cancel_post_carries_a_stable_request_id(monkeypatch):
    sandbox = common.Sandbox(
        {
            "PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com",
            "VENDOR_ACCOUNT_EMAIL": "v@x",
            "TREASURY_ACCOUNT_EMAIL": "t@x",
        }
    )
    posts = []

    def fake_call(method, path, **kw):
        if method == "POST":
            posts.append((path, kw.get("request_id")))
        return 400, "dbg", {}

    monkeypatch.setattr(spike5_unclaimed, "Sandbox", lambda: sandbox)
    monkeypatch.setattr(sandbox, "token", lambda app: ("tok", {}))
    monkeypatch.setattr(sandbox, "payout", lambda *a, **k: "BATCH-1")
    monkeypatch.setattr(sandbox, "poll_payout", lambda *a, **k: ("ITEM-1", ("ITEM-1", "UNCLAIMED")))
    monkeypatch.setattr(sandbox, "call", fake_call)

    assert spike5_unclaimed.main() == 0
    assert posts == [
        ("/v1/payments/payouts-item/ITEM-1/cancel", f"aegis-spike-s5-cancel-{sandbox.run}")
    ]
