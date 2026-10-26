"""SPIKE-11 verdict: a retry that creates a second batch FAILs even if the original is found."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import common  # noqa: E402
import spike11_recovery  # noqa: E402

ORIGINAL = {"batch_header": {"payout_batch_id": "BATCH-ORIGINAL", "batch_status": "PENDING"}}
SECOND = {"batch_header": {"payout_batch_id": "BATCH-SECOND", "batch_status": "PENDING"}}
DUP_ERROR = {"name": "USER_BUSINESS_ERROR", "details": [{"issue": "dup BATCH-ORIGINAL"}]}
LISTED = {"items": [{"payout_batch_id": "BATCH-ORIGINAL"}]}


def run_with(monkeypatch, posts):
    """posts: replies to create, same-key retry, new-key retry. GETs list the original."""
    queue = list(posts)

    class FakeSandbox(common.Sandbox):
        def __init__(self):
            super().__init__(
                {
                    "PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com",
                    "VENDOR_ACCOUNT_EMAIL": "v@x",
                }
            )

        def token(self, app):
            return "tok", []

        def call(self, method, path, **kw):
            if method == "POST":
                return queue.pop(0)
            if path.startswith("/v1/reporting"):
                return 200, "dbg", {"transaction_details": []}
            return 200, "dbg", LISTED

    monkeypatch.setattr(spike11_recovery, "Sandbox", FakeSandbox)
    return spike11_recovery.main()


def test_pass_when_retries_replay_or_are_rejected(monkeypatch):
    posts = [(201, "d", ORIGINAL), (201, "d", ORIGINAL), (400, "d", DUP_ERROR)]
    assert run_with(monkeypatch, posts) == 0


@pytest.mark.parametrize(
    "posts",
    [
        # same request id re-POST creates a new batch
        [(201, "d", ORIGINAL), (201, "d", SECOND), (400, "d", DUP_ERROR)],
        # new request id with the same sender_batch_id is accepted as a new batch
        [(201, "d", ORIGINAL), (201, "d", ORIGINAL), (201, "d", SECOND)],
    ],
)
def test_fail_when_any_retry_creates_a_second_batch(monkeypatch, posts):
    assert run_with(monkeypatch, posts) == 1
