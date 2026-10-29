"""R22: fail before clients, inspect the connected role, and close worker resources on SIGTERM."""

import asyncio
import signal
import uuid
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings, SettlementSettings, get_settlement_settings
from app.service_security import (
    API_VARIABLES,
    FRONTEND_VARIABLES,
    PROJECT_VARIABLES,
    SETTLEMENT_VARIABLES,
    validate_database_role,
    validate_service_environment,
)


def settlement_values(**overrides):
    return {
        "settlement_database_url": "postgres://worker:test@localhost/aegis",
        "treasury_client_id": "test-treasury",
        "treasury_client_secret": "treasury-private",
        "treasury_account_email": "treasury@test.example",
        "vendor_client_id": "test-vendor",
        "vendor_client_secret": "vendor-private",
        "vendor_account_email": "vendor@test.example",
        "audit_hmac_key": "test-audit-key-not-a-real-secret!!",
        **overrides,
    }


@pytest.mark.parametrize(
    "service,forbidden",
    [
        ("api", SETTLEMENT_VARIABLES | FRONTEND_VARIABLES),
        ("settlement", API_VARIABLES | FRONTEND_VARIABLES),
    ],
)
def test_other_service_variables_are_rejected_even_when_empty(service, forbidden):
    for field in forbidden:
        for value in ("", "never-echo-this-secret"):
            with pytest.raises(ValueError) as error:
                validate_service_environment(service, {field: value})
            assert field in str(error.value)
            assert "never-echo-this-secret" not in str(error.value)


def test_os_variables_and_same_service_variables_are_preserved():
    env = {"PATH": "/bin", "HOME": "/home/test", "PORT": "8000", "DATABASE_URL": "x"}
    validate_service_environment("api", env)
    assert env["PATH"] == "/bin" and env["DATABASE_URL"] == "x"
    validate_service_environment(
        "settlement", {"TREASURY_CLIENT_SECRET": "", "VENDOR_CLIENT_ID": "x"}
    )


@pytest.mark.parametrize("url", ["postgres://u:p@host/db", "postgresql://u:p@host/db"])
def test_render_urls_become_async_postgres_without_exposing_password(url):
    settings = Settings(_env_file=None, database_url=url)
    assert settings.database_url == "postgresql+asyncpg://u:p@host/db"
    worker = SettlementSettings(_env_file=None, **settlement_values())
    assert worker.settlement_database_url.startswith("postgresql+asyncpg://")
    assert isinstance(worker.treasury_client_secret, SecretStr)
    assert "treasury-private" not in repr(worker)
    assert "vendor-private" not in repr(worker)
    assert "u:p@" not in repr(settings)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"demo_replay_mode": True},
        {"demo_enabled": True, "paypal_base_url": "https://api-m.paypal.com"},
        {"paypal_base_url": "https://api-m.sandbox.paypal.com.evil.test"},
        {"paypal_base_url": "https://api-m.sandbox.paypal.com/v1"},
    ],
)
def test_demo_and_paypal_origin_fail_closed(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:", **kwargs)


def test_worker_requires_postgres_and_all_provider_credentials():
    with pytest.raises(ValidationError):
        SettlementSettings(_env_file=None, **settlement_values(settlement_database_url="sqlite://"))
    with pytest.raises(ValidationError):
        SettlementSettings(_env_file=None, **settlement_values(treasury_client_secret=" "))


def test_service_getters_never_load_shared_files_and_validation_errors_only_name_fields(
    monkeypatch,
):
    for field in PROJECT_VARIABLES:
        monkeypatch.delenv(field, raising=False)
    assert Settings.model_config.get("env_file") is None
    assert SettlementSettings.model_config.get("env_file") is None
    monkeypatch.setenv("SETTLEMENT_DATABASE_URL", "postgresql://user:secret-url@host/db")
    monkeypatch.setenv("TREASURY_CLIENT_SECRET", "secret-never-print")
    get_settlement_settings.cache_clear()
    try:
        with pytest.raises(ValueError) as error:
            get_settlement_settings()
        assert "treasury_client_id" in str(error.value)
        assert "secret-url" not in str(error.value)
        assert "secret-never-print" not in str(error.value)
    finally:
        get_settlement_settings.cache_clear()


async def test_api_env_rejection_precedes_database_construction(monkeypatch):
    from app import main

    monkeypatch.setenv("TREASURY_CLIENT_SECRET", "")

    def must_not_construct(*args):
        raise AssertionError("database must not be constructed")

    monkeypatch.setattr(main, "get_sessionmaker", must_not_construct)
    with pytest.raises(ValueError, match="TREASURY_CLIENT_SECRET"):
        async with main.lifespan(main.app):
            pass


def test_openapi_export_needs_no_settings_or_startup_connection(monkeypatch):
    from app import main

    def must_not_load():
        raise AssertionError("OpenAPI export must not boot the service")

    monkeypatch.setattr(main, "get_settings", must_not_load)
    assert "/api/executions/{execution_id}/decisions" in main.app.openapi()["paths"]


class RoleSession:
    def __init__(self, *, dialect="postgresql", **overrides):
        self.dialect = dialect
        self.row = {
            "rolcanlogin": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolbypassrls": False,
            "original_login": True,
            "api": True,
            "settlement": False,
            **overrides,
        }

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    async def execute(self, query, params):
        assert "current_user" in str(query) and "pg_has_role" in str(query)
        assert params["service"] in ("api", "settlement")
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: self.row))


@pytest.mark.parametrize(
    "unsafe",
    [
        {"rolsuper": True},
        {"rolcreatedb": True},
        {"rolcreaterole": True},
        {"rolbypassrls": True},
        {"rolreplication": True},
        {"settlement": True},
        {"api": False},
        {"original_login": False},
        {"rolcanlogin": False},
        {"dialect": "sqlite"},
    ],
)
async def test_role_check_rejects_privileged_shared_or_non_postgres_principals(unsafe):
    with pytest.raises(ValueError):
        await validate_database_role(RoleSession(**unsafe), "api")


async def test_each_service_requires_its_own_capability():
    await validate_database_role(RoleSession(), "api")
    await validate_database_role(RoleSession(api=False, settlement=True), "settlement")


@pytest.mark.parametrize("service,other", [("api", "settlement"), ("settlement", "api")])
async def test_role_validation_rejects_noninherited_cross_service_membership(
    pg_url, service, other
):
    admin = create_async_engine(pg_url)
    role = "startup_" + uuid.uuid4().hex[:12]
    login_engine = None
    try:
        async with admin.begin() as connection:
            await connection.execute(
                text(
                    f"CREATE ROLE {role} LOGIN PASSWORD 'local-test-only' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
            )
            await connection.execute(text(f"GRANT aegis_{service}_role TO {role}"))
        url = make_url(pg_url).set(username=role, password="local-test-only")
        login_engine = create_async_engine(url)
        async with AsyncSession(login_engine) as session:
            await validate_database_role(session, service)
            with pytest.raises(ValueError):
                await validate_database_role(session, other)
        async with admin.begin() as connection:
            await connection.execute(
                text(f"GRANT aegis_{other}_role TO {role} WITH INHERIT FALSE, SET TRUE")
            )
        async with AsyncSession(login_engine) as session:
            capabilities = (
                await session.execute(
                    text(
                        "SELECT pg_has_role(current_user, :role, 'USAGE'), "
                        "pg_has_role(current_user, :role, 'MEMBER')"
                    ),
                    {"role": f"aegis_{other}_role"},
                )
            ).one()
            assert tuple(capabilities) == (False, True)
            with pytest.raises(ValueError):
                await validate_database_role(session, service)
    finally:
        if login_engine is not None:
            await login_engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        await admin.dispose()


@pytest.mark.parametrize(
    "failure", [None, "role", "audit", "anchor", "runtime_anchor", "stop_during_audit"]
)
async def test_worker_sigterm_stops_all_loops_and_closes_clients_and_engine(
    monkeypatch, failure, caplog
):
    from app.audit.anchor import AnchorUnavailable
    from app.settlement import worker

    for field in PROJECT_VARIABLES:
        monkeypatch.delenv(field, raising=False)
    settings = SettlementSettings(_env_file=None, **settlement_values())
    monkeypatch.setattr(worker, "get_settlement_settings", lambda: settings)
    events, handlers = [], {}

    class Engine:
        async def dispose(self):
            events.append("dispose")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Client:
        def __init__(self, *, app, **kwargs):
            self.app = app
            events.append("client:" + app)

        async def aclose(self):
            events.append("close:" + self.app)

    async def validate(session, service):
        events.append("validate:" + service)
        if failure == "role":
            raise ValueError("wrong role")

    async def outbox(sessions, service, stop):
        events.append("outbox:start")
        await stop.wait()
        events.append("outbox:stop")

    async def poller(poller, sessions, stop):
        events.append("poller:start")
        await stop.wait()
        events.append("poller:stop")

    class Audit:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self):
            events.append("audit:verify")
            if failure == "stop_during_audit":
                handlers[signal.SIGTERM]()
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("audit:cancelled")
            if failure == "audit":
                raise RuntimeError("audit startup verification failed")
            if failure == "anchor":
                raise AnchorUnavailable("private transport details")

    async def audit_loop(consumer, stop):
        events.append("audit:start")
        if failure == "runtime_anchor":
            raise AnchorUnavailable("private transport details")
        await stop.wait()
        events.append("audit:stop")

    class Consumer:
        def __init__(self, *args):
            pass

        async def run_once(self):
            events.append("consumer:tick")
            if failure == "runtime_anchor":
                await asyncio.Event().wait()
            handlers[signal.SIGTERM]()

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: handlers.setdefault(sig, callback)
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: handlers.pop(sig))
    monkeypatch.setattr(signal, "signal", lambda *args: None)
    monkeypatch.setattr(worker, "create_async_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(worker, "async_sessionmaker", lambda *args, **kwargs: Session)
    monkeypatch.setattr(worker, "validate_database_role", validate)
    monkeypatch.setattr(worker, "PayPalClient", Client)
    monkeypatch.setattr(worker, "WebhookConsumer", Consumer)
    monkeypatch.setattr(worker, "AuditConsumer", Audit)
    monkeypatch.setattr(worker.audit_consumer, "run_forever", audit_loop)
    monkeypatch.setattr(worker.outbox_worker, "run_forever", outbox)
    monkeypatch.setattr(worker.reconciler, "run_forever", poller)
    if failure == "role":
        with pytest.raises(RuntimeError, match="startup validation failed"):
            await worker.run()
        assert events == ["validate:settlement", "dispose"]
    elif failure == "audit":
        with pytest.raises(RuntimeError, match="audit startup verification failed"):
            await worker.run()
        assert events == ["validate:settlement", "audit:verify", "dispose"]
    elif failure == "anchor":
        with pytest.raises(AnchorUnavailable):
            await worker.run()
        assert events == ["validate:settlement", "audit:verify", "dispose"]
        assert "audit_anchor_unavailable node=audit action=stop_worker" in caplog.text
        assert "private transport details" not in caplog.text
    elif failure == "runtime_anchor":
        with pytest.raises(ExceptionGroup) as error:
            await asyncio.wait_for(worker.run(), timeout=2)
        assert any(isinstance(exc, AnchorUnavailable) for exc in error.value.exceptions)
        assert events[-3:] == ["close:TREASURY", "close:VENDOR", "dispose"]
    elif failure == "stop_during_audit":
        await asyncio.wait_for(worker.run(), timeout=0.2)
        assert events == ["validate:settlement", "audit:verify", "audit:cancelled", "dispose"]
    else:
        await asyncio.wait_for(worker.run(), timeout=2)
        assert events.index("validate:settlement") < events.index("client:TREASURY")
        assert events.index("audit:verify") < events.index("client:TREASURY")
        assert {"outbox:stop", "poller:stop", "consumer:tick"} <= set(events)
        assert events[-3:] == ["close:TREASURY", "close:VENDOR", "dispose"]
    assert handlers == {}
