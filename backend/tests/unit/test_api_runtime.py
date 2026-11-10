"""The normal API lifespan starts graph checkpointing under an isolated runtime login."""

import uuid

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.main import lifespan
from app.service_security import PROJECT_VARIABLES


async def test_api_runtime_login_starts_checkpointed_graph(pg_url, pg_sessionmaker, monkeypatch):
    role = "api_runtime_" + uuid.uuid4().hex
    async with pg_sessionmaker.begin() as session:
        await session.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'synthetic-test-password'"))
        await session.execute(text(f"GRANT aegis_api_role TO {role}"))
    for field in PROJECT_VARIABLES:
        monkeypatch.delenv(field, raising=False)
    url = make_url(pg_url).set(username=role, password="synthetic-test-password")
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    monkeypatch.setenv("CHECKPOINT_DB_URL", url.render_as_string(hide_password=False))
    monkeypatch.setenv("SESSION_SECRET", "synthetic-runtime-session-secret-1234")
    get_settings.cache_clear()
    app = FastAPI()
    try:
        async with lifespan(app):
            assert app.state.dispatcher is not None
            async with app.state.sessions() as session:
                assert await session.scalar(text("SELECT current_user")) == role
                assert await session.scalar(
                    text("SELECT to_regclass('aegis_checkpoints.checkpoints') IS NOT NULL")
                )
    finally:
        get_settings.cache_clear()
        async with pg_sessionmaker.begin() as session:
            await session.execute(text(f"DROP OWNED BY {role}"))
            await session.execute(text(f"DROP ROLE {role}"))
