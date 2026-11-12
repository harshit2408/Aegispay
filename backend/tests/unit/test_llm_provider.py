"""Provider adapter: allowlist, fixed hosts, durable budget admission, fail-closed (R2, R14)."""

import json
from uuid import uuid4

import httpx
import psycopg
import pytest
import respx

from app.agents import ModelResponse, invoice_run
from app.agents.llm_allowlist import OUTPUT_CAPS, REVIEWED
from app.agents.provider import (
    DurableDispatch,
    ProviderUnavailable,
    governed_analysis,
    postgres_dsn,
)
from app.config import Settings

OPENAI = ("openai", "gpt-6.1-sol")
ROUTER = ("openrouter", "nvidia/nemotron-3.5-super-vl:free")
URLS = {key[0]: url for key, url in REVIEWED.items()}
ROUTER_CAP = OUTPUT_CAPS["openrouter"]
KEYS = {"openai": "sk-proj-sentinel-openai", "openrouter": "sk-or-v1-sentinel-router"}
KEY = KEYS["openrouter"]


def _body(**over):
    usage = {"prompt_tokens": 100, "completion_tokens": 20}
    body = {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
        "usage": usage,
    }
    body.update(over)
    return body


def _dispatch(pg_url, provider_model=ROUTER, bound=1000, run_id=None):
    run_id = run_id or uuid4()
    dispatch = DurableDispatch(run_id=run_id, database_url=pg_url, api_key=KEYS[provider_model[0]])
    dispatch.input_token_bound = bound
    return dispatch, run_id


def _row(pg_url, run_id):
    with psycopg.connect(postgres_dsn(pg_url)) as c:
        return c.execute(
            "SELECT requests, charged_micros, halted FROM llm_budgets WHERE run_id=%s",
            (str(run_id),),
        ).fetchone()


@pytest.mark.parametrize(
    ("provider", "model", "cap"),
    [
        ("openai", "gpt-6.1-sol", 2048),
        ("openrouter", ROUTER[1], 1024),
        ("openai", "gpt-6.1-sol", 4096),
        ("openai", "nvidia/nemotron-3.5-super-vl:free", 1024),
        ("openrouter", "gpt-6.1-sol", 4096),
        ("openrouter", "meta/other:free", 4096),
        ("evil", "gpt-6.1-sol", 1024),
        ("openrouter", ROUTER[1], None),
    ],
)
async def test_unreviewed_selection_is_refused_before_budget_or_http(pg_url, provider, model, cap):
    dispatch, run_id = _dispatch(pg_url)
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json=_body()))
        with pytest.raises(ProviderUnavailable):
            dispatch(provider, model, [], max_output_tokens=cap)
    assert not route.called
    assert _row(pg_url, run_id) is None


@pytest.mark.parametrize("selection", [OPENAI, ROUTER])
async def test_request_goes_only_to_the_providers_own_host(pg_url, selection):
    provider, model = selection
    dispatch, run_id = _dispatch(pg_url, selection)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(URLS[provider]).mock(return_value=httpx.Response(200, json=_body()))
        catch_all = router.route().mock(return_value=httpx.Response(500))
        response = dispatch(
            provider,
            model,
            [{"role": "user", "content": "x"}],
            max_output_tokens=OUTPUT_CAPS[provider],
        )
    assert isinstance(response, ModelResponse) and response.input_tokens == 100
    assert route.call_count == 1 and not catch_all.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer " + KEYS[provider]
    sent = json.loads(request.content)
    assert sent["model"] == model
    if provider == "openrouter":
        assert request.headers["x-title"] == "AegisPay"
        assert sent["max_tokens"] == ROUTER_CAP
        assert not {"store", "service_tier", "max_completion_tokens", "tools"} & sent.keys()
    else:
        assert sent["max_completion_tokens"] == 1024 and sent["store"] is False
        assert "x-title" not in request.headers


async def test_budget_is_charged_before_http_and_reconciled_after(pg_url):
    dispatch, run_id = _dispatch(pg_url, bound=1000)
    seen = {}

    def handler(request):
        seen["during"] = _row(pg_url, run_id)
        return httpx.Response(200, json=_body())

    with respx.mock as router:
        router.post(URLS["openrouter"]).mock(side_effect=handler)
        dispatch(*ROUTER, [], max_output_tokens=ROUTER_CAP)
    reserve = 2 * 1000 + 10 * ROUTER_CAP
    assert seen["during"] == (1, reserve, True)  # committed and halted while in flight
    assert _row(pg_url, run_id) == (1, 2 * 100 + 10 * 20, False)


async def test_fifth_request_and_over_cap_reserve_are_refused(pg_url):
    dispatch, run_id = _dispatch(pg_url, bound=1000)
    with respx.mock as router:
        router.post(URLS["openrouter"]).mock(return_value=httpx.Response(200, json=_body()))
        for _ in range(4):
            dispatch(*ROUTER, [], max_output_tokens=ROUTER_CAP)
        with pytest.raises(ProviderUnavailable, match="budget"):
            dispatch(*ROUTER, [], max_output_tokens=ROUTER_CAP)
    # Reserve 2*272000 + 10*4096 on top of 500000 already charged exceeds $1.00.
    big, big_id = _dispatch(pg_url, bound=272_000)
    with psycopg.connect(postgres_dsn(pg_url)) as c:
        c.execute(
            "INSERT INTO llm_budgets(run_id, charged_micros) VALUES (%s, 500000)", (str(big_id),)
        )
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json=_body()))
        with pytest.raises(ProviderUnavailable, match="budget"):
            big(*ROUTER, [], max_output_tokens=ROUTER_CAP)
    assert not route.called


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": {"message": "rate limited"}}),
        httpx.Response(500),
        httpx.Response(302, headers={"location": "https://attacker.example/steal"}),
        httpx.Response(200, json={"error": {"message": "provider error"}}),
        httpx.Response(200, json=_body(usage={})),
        httpx.Response(200, json=_body(usage={"prompt_tokens": 0, "completion_tokens": 1})),
        httpx.Response(200, json=_body(usage={"prompt_tokens": 5000, "completion_tokens": 1})),
        httpx.Response(200, json=_body(usage={"prompt_tokens": 10, "completion_tokens": 5000})),
        httpx.Response(200, json=_body(usage={"prompt_tokens": 10, "completion_tokens": True})),
        httpx.Response(
            200,
            json=_body(
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "completion_tokens_details": {"reasoning_tokens": 6},
                }
            ),
        ),
        httpx.Response(
            200,
            json=_body(
                choices=[
                    {"finish_reason": "length", "message": {"role": "assistant", "content": "{"}}
                ]
            ),
        ),
        httpx.Response(
            200,
            json=_body(
                choices=[
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": None, "tool_calls": [{}]},
                    }
                ]
            ),
        ),
        httpx.Response(
            200,
            json=_body(
                choices=[
                    {
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": "x", "tool_calls": [{}]},
                    }
                ]
            ),
        ),
    ],
)
async def test_bad_provider_outcome_fails_closed_and_stays_halted(pg_url, response):
    dispatch, run_id = _dispatch(pg_url, bound=1000)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(URLS["openrouter"]).mock(return_value=response)
        attacker = router.get("https://attacker.example/steal").mock(
            return_value=httpx.Response(200)
        )
        with pytest.raises(ProviderUnavailable):
            dispatch(*ROUTER, [], max_output_tokens=ROUTER_CAP)
    assert route.call_count == 1 and not attacker.called
    assert _row(pg_url, run_id)[2] is True  # sticky: no further admission


async def test_openrouter_null_usage_details_are_tolerated(pg_url):
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": None,
        "completion_tokens_details": None,
    }
    dispatch, _ = _dispatch(pg_url)
    with respx.mock as router:
        router.post(URLS["openrouter"]).mock(
            return_value=httpx.Response(200, json=_body(usage=usage))
        )
        response = dispatch(*ROUTER, [], max_output_tokens=ROUTER_CAP)
    assert (response.cached_tokens, response.reasoning_tokens) == (0, 0)


async def test_missing_key_or_bound_fails_before_budget(pg_url):
    run_id = uuid4()
    keyless = DurableDispatch(run_id=run_id, database_url=pg_url, api_key="")
    keyless.input_token_bound = 10
    with pytest.raises(ProviderUnavailable):
        keyless(*ROUTER, [], max_output_tokens=ROUTER_CAP)
    assert _row(pg_url, run_id) is None


@pytest.mark.parametrize(
    ("selection", "key"),
    [(OPENAI, "sk-or-v1-openrouter-key"), (ROUTER, "sk-proj-openai-key")],
)
async def test_key_is_never_sent_to_the_wrong_provider(pg_url, selection, key):
    run_id = uuid4()
    dispatch = DurableDispatch(run_id=run_id, database_url=pg_url, api_key=key)
    dispatch.input_token_bound = 100
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json=_body()))
        with pytest.raises(ProviderUnavailable, match="selected provider"):
            dispatch(*selection, [], max_output_tokens=OUTPUT_CAPS[selection[0]])
    assert not route.called and _row(pg_url, run_id) is None


def test_selection_follows_settings_and_unknown_values_fail_closed():
    base = {"database_url": "sqlite+aiosqlite:///:memory:"}
    assert Settings(**base).llm_provider == ""
    ok = Settings(**base, llm_provider="openrouter", llm_model_extract=ROUTER[1])
    from app.agents.provider import selected_llm

    assert selected_llm(Settings(**base)) == OPENAI
    assert selected_llm(ok) == ROUTER
    for bad in (
        {"llm_provider": "evil"},
        {"llm_provider": "openrouter", "llm_model_extract": "gpt-6.1-sol"},
        {"llm_model_reason": "x"},
    ):
        with pytest.raises(ProviderUnavailable):
            selected_llm(Settings(**base, **bad))


def test_run_is_keyed_to_the_selected_model_and_unreviewed_price_is_refused():
    seen = []

    def dispatch(provider, model, messages, max_output_tokens=None):
        seen.append((provider, model, max_output_tokens))
        return ModelResponse(content="ok", input_tokens=100, output_tokens=20)

    with invoice_run(uuid4(), dispatch=dispatch, llm=ROUTER) as run:
        run.complete("A2", [], input_token_bound=100)
        assert run.spent_micros == 400  # same conservative per-token price as the reviewed model
    assert seen == [("openrouter", ROUTER[1], ROUTER_CAP)]
    with pytest.raises(ValueError):
        with invoice_run(uuid4(), dispatch=dispatch, llm=("openrouter", "other:free")):
            pass


async def test_governed_analysis_with_unreviewed_selection_never_dispatches(pg_url):
    settings = Settings(
        database_url=pg_url, llm_api_key=KEY, llm_provider="openrouter", llm_model_extract="x:free"
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json=_body()))
        with pytest.raises(ProviderUnavailable):
            governed_analysis(uuid4(), settings=settings, action=lambda complete: complete)
    assert not route.called
