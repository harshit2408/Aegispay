"""Browser-only fixture: real HTTP routes/graph/Postgres, synthetic replay and no worker.

This replaces production lifespan, not production authentication or route handlers. It creates
and drops only its UUID-named database; never point TEST_PG_URL at a shared production server.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from app.agents.provider import postgres_dsn
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.decision.analyze import build_analyzer
from app.decision.approve import approve_for_payment
from app.decision.dispatch import Dispatcher
from app.decision.graph import build_graph
from app.demo.runtime import ReplayReader
from app.demo.seeder import OPERATORS, DemoSeed, seed_demo
from app.models.payables import POLedger, SettlementOutbox, SpendLedger
from app.settlement.po_ledger import reserve
from app.settlement.transitions import apply
from argon2 import PasswordHasher
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

PASSWORD = "browser-replay-fixture-password"
PG_BASE = os.environ.get(
    "TEST_PG_URL", "postgresql+asyncpg://postgres:postgres@localhost:55433"
)
DATABASE_NAME = "aegis_browser_" + uuid4().hex
DATABASE_URL = PG_BASE + "/" + DATABASE_NAME
os.environ.update(
    DATABASE_URL=DATABASE_URL,
    DEMO_ENABLED="true",
    DEMO_REPLAY_MODE="true",
    PAYPAL_BASE_URL="https://api-m.sandbox.paypal.com",
    SESSION_SECRET="synthetic-browser-session-secret-at-least-32-bytes",
    FRONTEND_ORIGIN="http://localhost:3100",
)
get_settings.cache_clear()


@asynccontextmanager
async def lifespan(app):
    admin = create_async_engine(PG_BASE + "/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{DATABASE_NAME}"'))
    sessions = get_sessionmaker(DATABASE_URL)
    try:
        os.environ["MIGRATION_DATABASE_URL"] = DATABASE_URL
        await asyncio.to_thread(
            command.upgrade,
            Config(str(Path(__file__).resolve().parents[2] / "backend/alembic.ini")),
            "head",
        )
        del os.environ["MIGRATION_DATABASE_URL"]
        with TemporaryDirectory(prefix="aegis-browser-") as storage:
            os.environ["STORAGE_PATH"] = storage
            get_settings.cache_clear()
            settings = get_settings()
            encoded = PasswordHasher().hash(PASSWORD)
            await seed_demo(
                sessions,
                config=DemoSeed(
                    run_id="browser",
                    vendor_payer_id="PAYERID123456",
                    vendor_email="supplier@aegispay-demo.example",
                    treasury_email="buyer@aegispay-demo.example",
                    onboarding_evidence={
                        "method": "synthetic replay",
                        "reference": "browser fixture",
                    },
                    operator_hashes={name: encoded for name in OPERATORS},
                ),
                settings=Settings(database_url=DATABASE_URL, demo_enabled=True, demo_replay_mode=False),
                manifest_dir=Path(storage) / "manifest",
            )
            dsn = postgres_dsn(DATABASE_URL)
            async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                await saver.setup()
                graph = build_graph(
                    saver,
                    sessions=sessions,
                    analyze=build_analyzer(sessions, settings),
                    reader=None,
                    replay_reader=ReplayReader(sessions, storage),
                    transition=apply,
                    approve=partial(approve_for_payment, sessions, apply, reserve),
                )
                app.state.sessions = sessions
                app.state.runtime_dsn = dsn
                app.state.dispatcher = Dispatcher(graph, sessions, dsn)
                app.state.run_case = app.state.dispatcher.run
                yield
                async with sessions() as session:
                    for model in (SpendLedger, POLedger, SettlementOutbox):
                        assert (
                            await session.scalar(
                                select(func.count()).select_from(model)
                            )
                            == 0
                        )
    finally:
        await sessions.kw["bind"].dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE "{DATABASE_NAME}" WITH (FORCE)')
            )
        await admin.dispose()


if __name__ == "__main__":
    # Import after synthetic settings so conditional demo router mounting stays real.
    from app.main import create_app

    app = create_app(demo_enabled=True)

    @app.get("/__test__/replay-invariants")
    async def replay_invariants():
        async with app.state.sessions() as session:
            counts = {}
            for key, model in (
                ("spend", SpendLedger),
                ("po", POLedger),
                ("outbox", SettlementOutbox),
            ):
                counts[key] = await session.scalar(
                    select(func.count()).select_from(model)
                )
            return counts

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="0.0.0.0", port=8100)
