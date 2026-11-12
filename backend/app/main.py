"""FastAPI entrypoint for the API process (PRD §6)."""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from functools import partial

from fastapi import FastAPI, HTTPException
from starlette.middleware.cors import CORSMiddleware

from app.agents.provider import analyst_completion, mandate_completion, postgres_dsn
from app.config import get_settings
from app.db import get_sessionmaker
from app.health import health
from app.paypal.client import PayPalClient
from app.routers import (
    analyst,
    auth,
    contracts,
    demo,
    documents,
    grns,
    mandates,
    ops,
    stream,
    vendors,
    webhooks,
)
from app.service_security import validate_database_role, validate_service_environment
from app.vendor_sim.gateway import Invoicing, VendorReader

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_service_environment("api")
    settings = get_settings()
    configure_demo_routes(app, enabled=settings.demo_enabled)
    sessions = get_sessionmaker(settings.database_url)
    engine = sessions.kw["bind"]
    app.state.sessions = sessions
    if settings.llm_api_key:
        app.state.mandate_complete = mandate_completion(settings)
        app.state.analyst_complete = analyst_completion(settings)
    app.state.runtime_dsn = postgres_dsn(settings.database_url)
    try:
        async with AsyncExitStack() as stack:
            try:
                async with sessions() as session:
                    await validate_database_role(session, "api")
            except Exception:
                raise RuntimeError("API database startup validation failed") from None
            if settings.checkpoint_db_url:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                from psycopg.conninfo import make_conninfo

                from app.decision.analyze import build_analyzer
                from app.decision.approve import approve_for_payment
                from app.decision.dispatch import Dispatcher
                from app.decision.graph import build_graph
                from app.decision.investigate import build_investigator
                from app.settlement.po_ledger import reserve
                from app.settlement.transitions import apply

                dsn = make_conninfo(
                    postgres_dsn(settings.checkpoint_db_url),
                    options="-c search_path=aegis_checkpoints,pg_catalog",
                )
                saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(dsn))
                await saver.conn.execute("SELECT pg_advisory_lock(9140010)")
                try:
                    await saver.setup()
                finally:
                    await saver.conn.execute("SELECT pg_advisory_unlock(9140010)")
                reader = None
                if settings.vendor_client_id and settings.vendor_client_secret:
                    client = await stack.enter_async_context(
                        PayPalClient(
                            app="vendor",
                            base_url=settings.paypal_base_url,
                            client_id=settings.vendor_client_id,
                            client_secret=settings.vendor_client_secret.get_secret_value(),
                        )
                    )
                    reader = VendorReader(Invoicing(client))
                gate_settings = {
                    "vendor_new_days": settings.vendor_new_days,
                    "max_vendor_invoices": settings.org_hard_max_invoices_per_vendor_per_day,
                }
                from app.demo.runtime import ReplayReader

                graph = build_graph(
                    saver,
                    sessions=sessions,
                    analyze=build_analyzer(sessions, settings),
                    reader=reader,
                    replay_reader=ReplayReader(sessions, settings.storage_path)
                    if settings.demo_replay_mode
                    else None,
                    # A4 only with a configured provider; replayed cases never call it.
                    investigator=build_investigator(sessions, settings)
                    if settings.llm_api_key
                    else None,
                    transition=apply,
                    approve=partial(approve_for_payment, sessions, apply, reserve, **gate_settings),
                    **gate_settings,
                )
                dispatcher = Dispatcher(graph, sessions, app.state.runtime_dsn)
                app.state.dispatcher = dispatcher
                app.state.vendor_reader = reader

                async def run_case(execution_id):
                    try:
                        await dispatcher.run(execution_id)
                    except Exception:
                        logger.exception("case dispatch stopped execution_id=%s", execution_id)

                app.state.run_case = run_case
                recovery = asyncio.create_task(dispatcher.recover_startup(run_case))
            else:
                recovery = None
            try:
                yield
            finally:
                if recovery is not None:
                    recovery.cancel()
                    with suppress(asyncio.CancelledError):
                        await recovery
    finally:
        await engine.dispose()


class RequestLimits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        # PayPal has a separate capped reader and raw-byte verification.
        if path.startswith("/api/webhooks/paypal/"):
            await self.app(scope, receive, send)
            return
        limit = documents.MAX_PDF_BYTES + 65536 if path.rstrip("/") == "/api/documents" else 65536
        total = 0

        async def capped_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, capped_receive, send)


class ConfiguredCORS:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and any(key == b"origin" for key, _ in scope["headers"]):
            middleware = CORSMiddleware(
                self.app,
                allow_origins=[get_settings().frontend_origin],
                allow_credentials=True,
                allow_methods=["GET", "POST"],
                allow_headers=["Content-Type", "X-CSRF-Token"],
            )
            await middleware(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def configure_demo_routes(app: FastAPI, *, enabled: bool) -> None:
    previous = getattr(app.state, "demo_routes", ())
    app.router.routes[:] = [route for route in app.router.routes if route not in previous]
    start = len(app.router.routes)
    if enabled:
        app.include_router(demo.router)
        app.include_router(webhooks.simulator_router)
    app.state.demo_routes = app.router.routes[start:]
    app.openapi_schema = None


def create_app(*, demo_enabled: bool = False) -> FastAPI:
    application = FastAPI(title="AegisPay API", lifespan=lifespan)
    application.add_middleware(RequestLimits)
    application.add_middleware(ConfiguredCORS)
    for router in (
        analyst,
        auth,
        contracts,
        documents,
        grns,
        mandates,
        ops,
        stream,
        vendors,
        webhooks,
    ):
        application.include_router(router.router)
    application.add_api_route("/api/health", health, methods=["GET"])
    configure_demo_routes(application, enabled=demo_enabled)
    return application


app = create_app()
