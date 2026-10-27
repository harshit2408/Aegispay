"""P1-03: OAuth cache per app, mandatory request_id, debug-id logging, same-id retries (R5)."""

import logging

import httpx
import pytest
import respx

from app.paypal.client import PayPalAuthError, PayPalClient, PayPalError

BASE = "https://api-m.sandbox.paypal.com"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _no_sleep(_: float) -> None:
    return None


def make_client(clock: Clock | None = None, **kw) -> PayPalClient:
    return PayPalClient(
        app="TREASURY",
        base_url=BASE,
        client_id="cid",
        client_secret="sec-ret",
        clock=clock or Clock(),
        sleep=_no_sleep,
        **kw,
    )


def token_route(router: respx.MockRouter, token: str = "tok1", expires_in: int = 3600):
    return router.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": token, "expires_in": expires_in})
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://api-m.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com.evil.test",
        "https://evil.test",
        "https://user@api-m.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com:8443",
    ],
)
def test_rejects_non_allowlisted_base_url(url):
    with pytest.raises(ValueError):
        PayPalClient(app="T", base_url=url, client_id="a", client_secret="b")


@respx.mock
async def test_token_cached_until_five_minutes_before_expiry():
    clock = Clock()
    tok = token_route(respx.mock, expires_in=1000)
    respx.get(f"{BASE}/v1/x").mock(return_value=httpx.Response(200, json={}))
    async with make_client(clock) as c:
        await c.get("/v1/x")
        clock.now += 600  # 400 s left, > 300 s margin
        await c.get("/v1/x")
        assert tok.call_count == 1
        clock.now += 150  # 250 s left, inside the margin
        await c.get("/v1/x")
        assert tok.call_count == 2


@respx.mock
async def test_oauth_uses_basic_auth_and_bearer_on_calls():
    tok = token_route(respx.mock)
    route = respx.get(f"{BASE}/v1/x").mock(return_value=httpx.Response(200, json={"a": 1}))
    async with make_client() as c:
        resp = await c.get("/v1/x")
    assert resp.body == {"a": 1}
    assert tok.calls[0].request.headers["authorization"].startswith("Basic ")
    assert tok.calls[0].request.content == b"grant_type=client_credentials"
    assert route.calls[0].request.headers["authorization"] == "Bearer tok1"


@respx.mock
async def test_each_app_has_its_own_token_cache():
    tok = token_route(respx.mock)
    respx.get(f"{BASE}/v1/x").mock(return_value=httpx.Response(200, json={}))
    async with make_client() as a, make_client() as b:
        await a.get("/v1/x")
        await b.get("/v1/x")
    assert tok.call_count == 2


@respx.mock
async def test_post_requires_request_id():
    token_route(respx.mock)
    async with make_client() as c:
        with pytest.raises(ValueError):
            await c.post("/v1/y", request_id="", json={})
        with pytest.raises(TypeError):
            await c.post("/v1/y", json={})  # type: ignore[call-arg]


@respx.mock
async def test_post_sends_request_id_and_retries_5xx_with_same_id():
    token_route(respx.mock)
    route = respx.post(f"{BASE}/v1/y").mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(201, json={"ok": 1}),
        ]
    )
    async with make_client() as c:
        resp = await c.post("/v1/y", request_id="aegis-e1-payout", json={"a": 1})
    assert resp.status == 201
    ids = {call.request.headers["paypal-request-id"] for call in route.calls}
    assert ids == {"aegis-e1-payout"}
    assert route.call_count == 3


@respx.mock
async def test_retries_exhausted_raises_last_error():
    token_route(respx.mock)
    route = respx.post(f"{BASE}/v1/y").mock(return_value=httpx.Response(500, json={"name": "X"}))
    async with make_client() as c:
        with pytest.raises(PayPalError) as exc:
            await c.post("/v1/y", request_id="r", json={})
    assert exc.value.status == 500
    assert route.call_count == 3


@respx.mock
async def test_timeout_after_retries_raises_transport_error():
    token_route(respx.mock)
    respx.post(f"{BASE}/v1/y").mock(side_effect=httpx.ReadTimeout("t"))
    async with make_client() as c:
        with pytest.raises(httpx.TransportError):
            await c.post("/v1/y", request_id="r", json={})


@respx.mock
async def test_4xx_is_not_retried_and_carries_body_and_debug_id():
    token_route(respx.mock)
    body = {"name": "USER_BUSINESS_ERROR", "details": [{"field": "SENDER_BATCH_ID"}]}
    route = respx.post(f"{BASE}/v1/y").mock(
        return_value=httpx.Response(400, json=body, headers={"paypal-debug-id": "dbg9"})
    )
    async with make_client() as c:
        with pytest.raises(PayPalError) as exc:
            await c.post("/v1/y", request_id="r", json={})
    assert route.call_count == 1
    assert exc.value.status == 400
    assert exc.value.body == body
    assert exc.value.debug_id == "dbg9"


@respx.mock
async def test_401_refreshes_token_once_then_succeeds():
    tok = respx.post(f"{BASE}/v1/oauth2/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "old", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "new", "expires_in": 3600}),
        ]
    )
    respx.get(f"{BASE}/v1/x").mock(side_effect=[httpx.Response(401), httpx.Response(200, json={})])
    async with make_client() as c:
        resp = await c.get("/v1/x")
    assert resp.status == 200
    assert tok.call_count == 2


@respx.mock
async def test_oauth_failure_raises_without_leaking_secret():
    respx.post(f"{BASE}/v1/oauth2/token").mock(
        return_value=httpx.Response(401, json={"error": "x"})
    )
    async with make_client() as c:
        with pytest.raises(PayPalAuthError) as exc:
            await c.get("/v1/x")
    assert "sec-ret" not in str(exc.value)


@respx.mock
async def test_debug_id_logged_with_execution_id_and_no_secrets(caplog):
    token_route(respx.mock, token="tok-secret")
    respx.post(f"{BASE}/v1/y").mock(
        return_value=httpx.Response(201, json={}, headers={"paypal-debug-id": "dbg1"})
    )
    caplog.set_level(logging.INFO, logger="aegis.paypal")
    async with make_client() as c:
        await c.post("/v1/y", request_id="aegis-e1-payout", json={}, execution_id="e1")
    text = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "dbg1" in text and "e1" in text
    assert "tok-secret" not in text and "sec-ret" not in text


@respx.mock
async def test_redirects_are_not_followed():
    token_route(respx.mock)
    respx.get(f"{BASE}/v1/x").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.test/"})
    )
    async with make_client() as c:
        with pytest.raises(PayPalError) as exc:
            await c.get("/v1/x")
    assert exc.value.status == 302


@respx.mock
async def test_401_after_two_5xx_still_refreshes_and_succeeds():
    token_route(respx.mock)
    respx.get(f"{BASE}/v1/x").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(401),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    async with make_client() as c:
        assert (await c.get("/v1/x")).status == 200


@respx.mock
async def test_post_without_retry_refreshes_401_but_returns_control_before_second_post():
    token_route(respx.mock)
    route = respx.post(f"{BASE}/v1/y").mock(
        side_effect=[httpx.Response(401), httpx.Response(500, json={"name": "X"})]
    )
    async with make_client() as c:
        with pytest.raises(PayPalError) as exc:
            await c.post("/v1/y", request_id="r", json={}, retry=False)
    assert exc.value.status == 401
    assert route.call_count == 1  # settlement must recheck before any second POST


@respx.mock
async def test_before_send_checks_after_token_acquisition_and_can_refuse_the_post():
    token = token_route(respx.mock)
    payout = respx.post(f"{BASE}/v1/y")

    async def halted():
        assert token.call_count == 1
        raise ValueError("halted")

    async with make_client() as client:
        with pytest.raises(ValueError, match="halted"):
            await client.post("/v1/y", request_id="r", retry=False, before_send=halted)
    assert payout.call_count == 0
