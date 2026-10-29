"""FastAPI entrypoint for the `api` service (PRD §6)."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.routers import contracts, stream, webhooks
from app.service_security import validate_database_role, validate_service_environment

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_service_environment("api")
    settings = get_settings()
    sessions = get_sessionmaker(settings.database_url)
    engine = sessions.kw["bind"]
    try:
        try:
            async with sessions() as session:
                await validate_database_role(session, "api")
        except Exception:
            raise RuntimeError("API database startup validation failed") from None
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="AegisPay API", lifespan=lifespan)
app.include_router(contracts.router)
app.include_router(stream.router)
app.include_router(webhooks.router)
app.include_router(webhooks.simulator_router)  # 404s unless DEMO_ENABLED (R24); unmounted in P3-15


# Liveness + DB ping only; the full PRD §13 health checks come in a later checklist item.
@app.get("/api/health")
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health: database check failed")
        return JSONResponse(status_code=503, content={"status": "error", "db": False})
    return JSONResponse(content={"status": "ok", "db": True})
