"""Async PayPal REST client: one instance per app, so one OAuth token cache per app (PRD §9.1).

Every POST requires a caller-supplied `request_id` and every retry reuses it (R5). A caller that
must recheck state before each attempt (the payout POST, R19/R20) passes `retry=False` and retries
itself. The base URL is
an exact HTTPS host allowlist and redirects are never followed, so tokens cannot leave PayPal.
"""

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("aegis.paypal")

ALLOWED_HOSTS = frozenset(
    {"api-m.sandbox.paypal.com", "api.sandbox.paypal.com", "api-m.paypal.com", "api.paypal.com"}
)
TOKEN_REFRESH_MARGIN_S = 300  # PRD §9.1: refresh when now >= expires_at - 300 s
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5
TIMEOUT_S = 30.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class PayPalResponse:
    status: int
    debug_id: str | None
    body: dict[str, Any]


class PayPalError(Exception):
    """Non-2xx PayPal answer. `body` is PayPal's error JSON (never contains our credentials)."""

    def __init__(self, status: int, debug_id: str | None, body: dict[str, Any]) -> None:
        super().__init__(f"PayPal HTTP {status} {body.get('name', '')} debug_id={debug_id}")
        self.status = status
        self.debug_id = debug_id
        self.body = body


class PayPalAuthError(Exception):
    """OAuth token request failed. Carries no response body: it could echo credentials."""


def _validated_base_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.port is not None
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"PayPal base URL must be https://<{sorted(ALLOWED_HOSTS)}>, got {url!r}")
    return f"https://{parts.hostname}"


def _json_or_raw(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {"raw": resp.text[:300]}
    return body if isinstance(body, dict) else {"raw": body}


class PayPalClient:
    def __init__(
        self,
        *,
        app: str,
        base_url: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.app = app
        self._base = _validated_base_url(base_url)
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._basic = f"Basic {basic}"
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
        self._owns_http = http is None
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> "PayPalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def get(
        self, path: str, *, params: dict[str, str] | None = None, execution_id: str | None = None
    ) -> PayPalResponse:
        return await self._request("GET", path, params=params, execution_id=execution_id)

    async def post(
        self,
        path: str,
        *,
        request_id: str,
        json: dict[str, Any] | None = None,
        execution_id: str | None = None,
        retry: bool = True,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> PayPalResponse:
        """`retry=False` sends once; a 401 refreshes auth and returns control to the caller."""
        if not request_id or not request_id.strip():
            raise ValueError("PayPal POST requires a non-empty request_id (R5)")
        return await self._request(
            "POST",
            path,
            json=json,
            request_id=request_id,
            execution_id=execution_id,
            max_attempts=MAX_ATTEMPTS if retry else 1,
            before_send=before_send,
        )

    async def _access_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if (
                force
                or self._token is None
                or self._clock() >= self._expires_at - TOKEN_REFRESH_MARGIN_S
            ):
                await self._fetch_token()
            assert self._token is not None
            return self._token

    async def _fetch_token(self) -> None:
        resp = await self._http.post(
            f"{self._base}/v1/oauth2/token",
            headers={"Authorization": self._basic, "Accept": "application/json"},
            content=b"grant_type=client_credentials",
        )
        if resp.status_code != 200:
            log.error(
                "oauth failed app=%s status=%s debug_id=%s",
                self.app,
                resp.status_code,
                resp.headers.get("paypal-debug-id"),
            )
            raise PayPalAuthError(f"{self.app} OAuth failed: HTTP {resp.status_code}")
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 0))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        request_id: str | None = None,
        execution_id: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> PayPalResponse:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("path must be relative to the configured PayPal base URL")
        refreshed = False
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            headers = {
                "Authorization": f"Bearer {await self._access_token()}",
                "Accept": "application/json",
            }
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            if before_send is not None:
                await before_send()  # auth can perform HTTP: caller checks after it, before POST
            try:
                resp = await self._http.request(
                    method, self._base + path, params=params, json=json, headers=headers
                )
            except httpx.TransportError:
                log.warning(
                    "transport error app=%s %s %s attempt=%s execution_id=%s",
                    self.app,
                    method,
                    path,
                    attempt,
                    execution_id,
                )
                if attempt == max_attempts:
                    raise
                await self._sleep(BACKOFF_BASE_S * 2 ** (attempt - 1))
                continue
            debug_id = resp.headers.get("paypal-debug-id")
            log.info(
                "paypal app=%s %s %s status=%s debug_id=%s execution_id=%s",
                self.app,
                method,
                path,
                resp.status_code,
                debug_id,
                execution_id,
            )
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                await self._access_token(force=True)
                if max_attempts > 1:
                    attempt -= 1
                    continue
            if resp.status_code in RETRY_STATUSES and attempt < max_attempts:
                await self._sleep(BACKOFF_BASE_S * 2 ** (attempt - 1))
                continue
            body = _json_or_raw(resp) if resp.content else {}
            if resp.status_code >= 300:
                raise PayPalError(resp.status_code, debug_id, body)
            return PayPalResponse(resp.status_code, debug_id, body)
        raise AssertionError("unreachable")  # pragma: no cover
