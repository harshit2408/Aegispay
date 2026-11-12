"""R24: disabled demo routes do not exist in routing or the public schema."""

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import configure_demo_routes, create_app


def test_demo_routes_mount_only_when_enabled_and_do_not_accumulate():
    app = create_app()
    for enabled in (False, True, True, False):
        configure_demo_routes(app, enabled=enabled)
        paths = app.openapi()["paths"]
        assert ("/api/demo/reset" in paths) is enabled
        assert ("/api/webhooks/paypal/simulator/{account}" in paths) is enabled
        assert len(app.routes) == len({id(route) for route in app.routes})
        assert sum(getattr(route, "path", None) == "/api/demo/reset" for route in app.routes) <= 1


async def test_disabled_demo_has_no_method_or_schema_discovery():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        for path in (
            "/api/demo/reset",
            "/api/demo/scenarios/S1/run",
            "/api/demo/forensics-detector",
        ):
            for method in ("GET", "POST", "OPTIONS"):
                response = await client.request(method, path)
                assert response.status_code == 404, (method, path)
    assert not any(path.startswith("/api/demo/") for path in app.openapi()["paths"])


@pytest.mark.parametrize("replay", [False, True])
def test_live_origin_refuses_every_demo_mode(replay):
    with pytest.raises(ValidationError, match="demo requires sandbox PayPal"):
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            paypal_base_url="https://api-m.paypal.com",
            demo_enabled=True,
            demo_replay_mode=replay,
        )
