import json

import pytest

from app.agents.extractor import extract_invoice
from app.agents.replay import ReplayComplete, replay_complete
from app.config import Settings

from .test_agent_extractor import GOOD

DB = "sqlite+aiosqlite://"


def settings(**kw):
    return Settings(_env_file=None, database_url=DB, **kw)


def test_refuses_without_replay_mode():
    with pytest.raises(ValueError, match="DEMO_REPLAY_MODE"):
        replay_complete(settings(demo_enabled=True), {"A2": [json.dumps(GOOD)]})
    with pytest.raises(ValueError, match="DEMO_REPLAY_MODE"):
        replay_complete(settings(), {})


def test_live_url_cannot_enable_replay():
    with pytest.raises(ValueError):
        settings(
            demo_enabled=True, demo_replay_mode=True, paypal_base_url="https://api-m.paypal.com"
        )


def test_replays_in_order_tagged_and_feeds_extractor():
    s = settings(demo_enabled=True, demo_replay_mode=True)
    c = replay_complete(s, {"A2": [json.dumps(GOOD)]})
    assert c.replayed is True
    r = extract_invoice(c, text="Invoice")
    assert r.extraction.total_cents == 10000 and c.served == ["A2"]


def test_missing_recording_fails_closed():
    c = ReplayComplete({})
    r = extract_invoice(c, text="Invoice")
    assert r.freeze_reason is not None
    with pytest.raises(LookupError):
        c("A3", [], input_token_bound=1)
