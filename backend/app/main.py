"""FastAPI entrypoint for the `api` service (PRD §6)."""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

logger = logging.getLogger(__name__)

app = FastAPI(title="AegisPay API")


# Liveness + DB ping only; the full PRD §13 health checks come in a later checklist item.
@app.get("/api/health")
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health: database check failed")
        return JSONResponse(status_code=503, content={"status": "error", "db": False})
    return JSONResponse(content={"status": "ok", "db": True})
