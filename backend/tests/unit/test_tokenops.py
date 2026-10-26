import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from uuid import uuid4

import pytest

from app.agents import Halt, ModelResponse, invoice_run


def test_four_calls_share_one_budget_and_fifth_is_blocked(caplog):
    calls = []

    def dispatch(provider, model, messages, max_output_tokens=None):
        calls.append((provider, model, max_output_tokens))
        return ModelResponse(content="private response", input_tokens=100, output_tokens=20)

    with caplog.at_level("INFO"), invoice_run(uuid4(), dispatch=dispatch) as run:
        for node in ("A2", "A3", "A4", "A5"):
            assert run.complete(node, ["private prompt"], input_token_bound=100).content
        assert run.spent_micros == 1600
        with pytest.raises(Halt, match="four"):
            run.complete("A4", [], input_token_bound=100)
    assert len(calls) == 4
    assert all(call == ("openai", "gpt-6.1-sol", 1024) for call in calls)
    assert "private prompt" not in caplog.text
    assert "private response" not in caplog.text
    assert '"node": "A5"' in caplog.text


def test_worst_case_refuses_next_agent_before_dispatch():
    calls = []

    def dispatch(*args, **kwargs):
        calls.append(1)
        return ModelResponse(content="ok", input_tokens=250_000, output_tokens=1000)

    with invoice_run(uuid4(), dispatch=dispatch) as run:
        run.complete("A2", [], input_token_bound=250_000)
        assert run.spent_micros == 510_000
        with pytest.raises(Halt, match="worst-case"):
            run.complete("A3", [], input_token_bound=250_000)
        with pytest.raises(Halt):
            run.complete("A3", [], input_token_bound=1)
    assert calls == [1]


def test_provider_error_halts_without_exposing_error_text():
    calls = []

    def dispatch(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("private API key")

    with invoice_run(uuid4(), dispatch=dispatch) as run:
        with pytest.raises(Halt, match="uncertain") as caught:
            run.complete("A2", [], input_token_bound=100)
        assert "private API key" not in "".join(traceback.format_exception(caught.value))
        with pytest.raises(Halt):
            run.complete("A2", [], input_token_bound=100)
    assert calls == [1]


@pytest.mark.parametrize("bound", [None, True, -1, 0, 272_001, 2.5])
def test_invalid_input_bound_never_dispatches(bound):
    def dispatch(*args, **kwargs):
        pytest.fail("must not dispatch")

    with invoice_run(uuid4(), dispatch=dispatch) as run, pytest.raises(ValueError):
        run.complete("A2", [], input_token_bound=bound)


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse("ok", input_tokens=-1),
        ModelResponse("ok", input_tokens=101),
        ModelResponse("ok", input_tokens=1, output_tokens=1025),
        ModelResponse("ok", input_tokens=1, cached_tokens=2),
        ModelResponse("ok", input_tokens=1, output_tokens=True),
        object(),
    ],
)
def test_invalid_or_underestimated_usage_halts(response):
    with invoice_run(uuid4(), dispatch=lambda *a, **k: response) as run:
        with pytest.raises(Halt):
            run.complete("A2", [], input_token_bound=100)
        with pytest.raises(Halt):
            run.complete("A3", [], input_token_bound=100)


def test_run_cannot_be_used_after_exit():
    with invoice_run(uuid4(), dispatch=lambda *a, **k: None) as run:
        pass
    with pytest.raises(RuntimeError, match="closed"):
        run.complete("A2", [], input_token_bound=100)


def test_chronicle_recording_and_environment_export_are_disabled(monkeypatch):
    import chronicle

    monkeypatch.setenv("CHRONICLE_CONTROL_PLANE_URL", "http://must-not-contact.invalid")

    def dispatch(*args, **kwargs):
        session = chronicle.current_session()
        assert session.settings.recording is False
        assert session.settings.capture_input is False
        assert session.settings.capture_output is False
        assert session.sink is None
        return ModelResponse("ok", input_tokens=1, output_tokens=1)

    with invoice_run(uuid4(), dispatch=dispatch) as run:
        run.complete("A2", [], input_token_bound=100)


def test_inclusive_cached_and_reasoning_usage_is_not_billed_twice():
    response = ModelResponse(
        "ok", input_tokens=100, output_tokens=50, cached_tokens=80, reasoning_tokens=40
    )
    with invoice_run(uuid4(), dispatch=lambda *a, **k: response) as run:
        run.complete("A2", [], input_token_bound=100)
        assert run.spent_micros == 700
        window = run._bound.governor.ledger.runs[run._bound.attr.trace_id].window
        assert window[-1].tags["model"] == "gpt-6.1-sol"
        assert window[-1].boundary_id == "A2.chat"


def test_cancellation_marks_run_halted():
    calls = []

    def dispatch(*args, **kwargs):
        calls.append(1)
        raise KeyboardInterrupt()

    with invoice_run(uuid4(), dispatch=dispatch) as run:
        with pytest.raises(KeyboardInterrupt):
            run.complete("A2", [], input_token_bound=100)
        with pytest.raises(Halt):
            run.complete("A2", [], input_token_bound=100)
    assert calls == [1]


def test_invalid_execution_id_and_node_rejected():
    with pytest.raises(ValueError):
        with invoice_run("private document text", dispatch=lambda *a: None):
            pytest.fail("invalid execution id")
    with invoice_run(uuid4(), dispatch=lambda *a: None) as run, pytest.raises(ValueError):
        run.complete("A1", [], input_token_bound=100)


def test_recursion_is_rejected_before_a_second_dispatch():
    calls = []

    def dispatch(*args, **kwargs):
        calls.append(1)
        run.complete("A3", [], input_token_bound=100)

    with invoice_run(uuid4(), dispatch=dispatch) as run, pytest.raises(Halt):
        run.complete("A2", [], input_token_bound=100)
    assert calls == [1]


def test_ambient_preview_cannot_disable_enforcement():
    from tokenops import RequestContext, bind_request_context, clear_request_context

    calls = []

    def dispatch(provider, model, messages, max_output_tokens=None):
        calls.append(max_output_tokens)
        return ModelResponse("ok", input_tokens=250_000, output_tokens=1000)

    bind_request_context(
        RequestContext(
            headers={"traceparent": "untrusted"},
            payload={"user": "private"},
            service="untrusted",
            mode="preview",
            user_dims={"tenant": "private"},
        )
    )
    try:
        with invoice_run(uuid4(), dispatch=dispatch) as run:
            run.complete("A2", [], input_token_bound=250_000)
            with pytest.raises(Halt):
                run.complete("A3", [], input_token_bound=250_000)
            assert run.spent_micros == 510_000
            assert "private" not in str(run._bound.attr)
    finally:
        clear_request_context()
    assert calls == [1024]


def test_nested_run_cannot_spend_through_outer_handle():
    calls = []

    def dispatch(*args, **kwargs):
        calls.append(1)
        return ModelResponse("ok", input_tokens=250_000, output_tokens=1000)

    with invoice_run(uuid4(), dispatch=dispatch) as outer:
        outer.complete("A2", [], input_token_bound=250_000)
        with invoice_run(uuid4(), dispatch=dispatch):
            with pytest.raises(RuntimeError, match="different run scope"):
                outer.complete("A3", [], input_token_bound=250_000)
        with pytest.raises(Halt):
            outer.complete("A3", [], input_token_bound=250_000)
    assert calls == [1]


def test_import_disables_library_dotenv_loading(tmp_path):
    code = """
import os
import re
import re
import dotenv
real = dotenv.load_dotenv

def guarded(*args, **kwargs):
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    # The disabled loader returns before opening either candidate path.
    kwargs["dotenv_path"] = "nonexistent-tokenops-import-test"
    assert real(**kwargs) is False
    return False

dotenv.load_dotenv = guarded
import app.agents
assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
"""
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "0"}
    subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("response", "cost"),
    [
        (ModelResponse("ok", input_tokens=101), 202),
        (ModelResponse("ok", input_tokens=1, output_tokens=1025), 10_252),
    ],
)
def test_over_bound_usage_is_logged_and_charged_before_halt(caplog, response, cost):
    with caplog.at_level("INFO"), invoice_run(uuid4(), dispatch=lambda *a, **k: response) as run:
        with pytest.raises(Halt):
            run.complete("A2", [], input_token_bound=100)
        assert run.spent_micros == cost
    records = [json.loads(r.getMessage()) for r in caplog.records if "llm_usage" in r.getMessage()]
    assert records == [
        {
            "event": "llm_usage",
            "execution_id": records[0]["execution_id"],
            "node": "A2",
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "cost_micros": cost,
            "rejected": True,
        }
    ]


def test_logged_cost_matches_ledger_charge(caplog):
    response = ModelResponse(
        "ok", input_tokens=100, output_tokens=50, cached_tokens=80, reasoning_tokens=40
    )
    with caplog.at_level("INFO"), invoice_run(uuid4(), dispatch=lambda *a, **k: response) as run:
        run.complete("A2", [], input_token_bound=100)
        spent = run.spent_micros
    logged = [json.loads(r.getMessage()) for r in caplog.records if "llm_usage" in r.getMessage()]
    assert [r["cost_micros"] for r in logged] == [spent]


def test_only_app_agents_imports_tokenops():
    # app.agents disables TokenOps' import-time dotenv loading; an earlier import elsewhere
    # would bypass that, so no other app module may import tokenops directly.
    app_dir = Path(__file__).resolve().parents[2] / "app"
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.parent.name != "agents"
        and re.search(r"^\s*(from|import)\s+tokenops\b", path.read_text(), re.M)
    ]
    assert offenders == []
