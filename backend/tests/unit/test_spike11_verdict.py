"""SPIKE-11 verdict: a retry that creates a second batch FAILs even if the original is found."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "spikes"))

import common  # noqa: E402
import spike11_recovery  # noqa: E402

ORIGINAL = {"batch_header": {"payout_batch_id": "BATCHORIGINAL", "batch_status": "PENDING"}}
SECOND = {"batch_header": {"payout_batch_id": "BATCHSECOND", "batch_status": "PENDING"}}
DUP_ERROR = {
    "name": "USER_BUSINESS_ERROR",
    "details": [
        {
            "field": "SENDER_BATCH_ID",
            "link": [
                {
                    "href": "https://api.sandbox.paypal.com/v1/payments/payouts/BATCHORIGINAL",
                    "rel": "self",
                    "method": "GET",
                }
            ],
        }
    ],
}


def run_with(monkeypatch, posts, *, mismatch=None):
    """Use the measured 404 list responses and a matching batch GET by default."""
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
            if path == "/v1/payments/payouts/BATCHORIGINAL":
                item = self.payout_body("v@x", common.SPIKE_AMOUNT, "s11")["items"][0]
                if mismatch:
                    item[mismatch[0]] = mismatch[1]
                return (
                    200,
                    "dbg",
                    {
                        "batch_header": {
                            **ORIGINAL["batch_header"],
                            "sender_batch_header": {
                                "sender_batch_id": f"AEGIS-SPIKE-s11-{self.run}"
                            },
                        },
                        "items": [{"payout_item": item}],
                    },
                )
            assert path.startswith("/v1/payments/payouts"), path
            return 404, "dbg", {}

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


def test_reject_batch_id_mentioned_without_a_recovery_link(monkeypatch):
    error = {"name": "USER_BUSINESS_ERROR", "details": [{"issue": "dup BATCHORIGINAL"}]}
    assert run_with(monkeypatch, [(201, "d", ORIGINAL), (400, "d", error), (400, "d", error)]) == 1


@pytest.mark.parametrize(
    "mismatch",
    [
        ("receiver", "foreign@x"),
        ("amount", {"currency": "USD", "value": "2.00"}),
        ("recipient_type", "PAYPAL_ID"),
        ("sender_item_id", "foreign-item"),
    ],
)
def test_recovery_requires_get_to_match_frozen_command(monkeypatch, mismatch):
    posts = [(201, "d", ORIGINAL), (400, "d", DUP_ERROR), (400, "d", DUP_ERROR)]
    assert run_with(monkeypatch, posts, mismatch=mismatch) == 1


@pytest.mark.parametrize("host", ["api.sandbox.paypal.com", "api-m.sandbox.paypal.com"])
def test_duplicate_link_accepts_only_recorded_sandbox_origins(host):
    error = copy.deepcopy(DUP_ERROR)
    error["details"][0]["link"][0]["href"] = f"https://{host}/v1/payments/payouts/BATCHORIGINAL"
    assert spike11_recovery.duplicate_batch_id(error) == "BATCHORIGINAL"


@pytest.mark.parametrize(
    "href",
    [
        "http://api.sandbox.paypal.com/v1/payments/payouts/BATCHORIGINAL",
        "https://api.paypal.com/v1/payments/payouts/BATCHORIGINAL",
        "https://api.sandbox.paypal.com.evil.example/v1/payments/payouts/BATCHORIGINAL",
        "https://user@api.sandbox.paypal.com/v1/payments/payouts/BATCHORIGINAL",
        "https://api.sandbox.paypal.com:443/v1/payments/payouts/BATCHORIGINAL",
        "https://api.sandbox.paypal.com/v1/payments/payouts/BATCHORIGINAL?x=1",
        "https://api.sandbox.paypal.com/v1/payments/payouts/BATCHORIGINAL#fragment",
        "/v1/payments/payouts/BATCHORIGINAL",
        None,
    ],
)
def test_duplicate_link_rejects_untrusted_or_malformed_urls(href):
    error = copy.deepcopy(DUP_ERROR)
    error["details"][0]["link"][0]["href"] = href
    assert spike11_recovery.duplicate_batch_id(error) is None


@pytest.mark.parametrize(
    "error",
    [
        None,
        [],
        {},
        {"name": "USER_BUSINESS_ERROR", "details": None},
        {
            "name": "USER_BUSINESS_ERROR",
            "details": [None, {"field": "SENDER_BATCH_ID", "link": None}],
        },
    ],
)
def test_duplicate_link_rejects_malformed_error_shape(error):
    assert spike11_recovery.duplicate_batch_id(error) is None


def test_recovered_batch_rejects_wrong_header_or_extra_items():
    command = common.Sandbox({"PAYPAL_BASE_URL": "https://api.sandbox.paypal.com"}).payout_body(
        "v@x", common.SPIKE_AMOUNT, "s11"
    )
    response = {
        "batch_header": {
            **ORIGINAL["batch_header"],
            "sender_batch_header": command["sender_batch_header"],
        },
        "items": [{"payout_item": command["items"][0]}],
    }
    assert spike11_recovery.batch_matches_command(response, command, "BATCHORIGINAL")
    extra = copy.deepcopy(response)
    extra["items"].append(extra["items"][0])
    assert not spike11_recovery.batch_matches_command(extra, command, "BATCHORIGINAL")
    for field in ("payout_batch_id", "sender_batch_header"):
        altered = copy.deepcopy(response)
        altered["batch_header"][field] = "foreign"
        assert not spike11_recovery.batch_matches_command(altered, command, "BATCHORIGINAL")
