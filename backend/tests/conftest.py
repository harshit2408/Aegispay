import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Each test sees a fresh Settings built from its own env."""
    monkeypatch.setenv("DATABASE_URL", SQLITE_URL)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Postgres fixtures: real Postgres for schema, locking and concurrency tests (ADR-002) -------
PG_BASE = os.environ.get("TEST_PG_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:55433")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _admin(url: str):
    return create_async_engine(url + "/postgres", isolation_level="AUTOCOMMIT")


async def _run(url: str, sql: str) -> None:
    engine = _admin(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def pg_template():
    """A migrated template database; each test clones it. Skips locally without Postgres."""
    name = f"aegis_tpl_{uuid.uuid4().hex[:8]}"
    try:
        asyncio.run(_run(PG_BASE, f'CREATE DATABASE "{name}"'))
    except (OSError, SQLAlchemyError) as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable at {PG_BASE}: {exc.__class__.__name__}")
    os.environ["MIGRATION_DATABASE_URL"] = f"{PG_BASE}/{name}"
    try:
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    finally:
        del os.environ["MIGRATION_DATABASE_URL"]
    yield name
    asyncio.run(_run(PG_BASE, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def pg_url(pg_template):
    name = f"aegis_t_{uuid.uuid4().hex[:10]}"
    await _run(PG_BASE, f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    yield f"{PG_BASE}/{name}"
    await _run(PG_BASE, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def pg_sessionmaker(pg_url):
    engine = create_async_engine(pg_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
