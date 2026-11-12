"""R25 opaque sessions: fresh roles, bounded lifetime and token-bound CSRF."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.auth import AuthAttempt, OperatorSession
from app.models.core import Operator

COOKIE = "aegis_session"
IDLE_TTL = timedelta(minutes=30)
ABSOLUTE_TTL = timedelta(hours=8)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    secret = get_settings().session_secret
    if secret is None:
        raise HTTPException(503, "operator sessions unavailable")
    return hmac.new(
        secret.get_secret_value().encode(), b"csrf:" + token.encode(), "sha256"
    ).hexdigest()


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def bucket_hit(session: AsyncSession, key: str, now: datetime) -> int:
    """Count one attempt in a persistent one-minute bucket; the caller commits."""
    await session.execute(
        insert(AuthAttempt)
        .values(bucket_key=key, window_started=now, attempts=0)
        .on_conflict_do_nothing()
    )
    row = await session.get(AuthAttempt, key, with_for_update=True, populate_existing=True)
    if utc(row.window_started) + timedelta(minutes=1) <= now:
        row.window_started, row.attempts = now, 0
    row.attempts += 1
    return row.attempts


async def require_operator(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Operator:
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "session required")
    now = datetime.now(UTC)
    row = await session.get(OperatorSession, token_hash(token), populate_existing=True)
    if row is None or utc(row.expires_at) <= now or utc(row.last_seen_at) + IDLE_TTL <= now:
        await session.rollback()
        raise HTTPException(401, "session expired")
    operator = await session.get(Operator, row.operator_id, populate_existing=True)
    if operator is None or operator.disabled:
        await session.rollback()
        raise HTTPException(401, "session unavailable")
    row.last_seen_at = now
    await session.commit()
    return operator


async def require_csrf(
    request: Request, operator: Annotated[Operator, Depends(require_operator)]
) -> Operator:
    provided = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(provided, csrf_token(request.cookies.get(COOKIE, ""))):
        raise HTTPException(403, "CSRF token required")
    return operator


def roles(*allowed: str):
    async def eligible(operator: Annotated[Operator, Depends(require_csrf)]) -> Operator:
        if operator.role not in allowed:
            raise HTTPException(403, "role not permitted")
        return operator

    return eligible
