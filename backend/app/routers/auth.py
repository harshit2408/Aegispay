"""Operator login, logout and session identity (R18, R25)."""

import asyncio
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ABSOLUTE_TTL,
    COOKIE,
    csrf_token,
    require_csrf,
    require_operator,
    token_hash,
    utc,
)
from app.config import get_settings
from app.db import get_session
from app.models.auth import AuthAttempt, OperatorSession
from app.models.core import Operator

router = APIRouter(prefix="/api/auth", tags=["auth"])
Session = Annotated[AsyncSession, Depends(get_session)]
_HASHER = PasswordHasher()
_DUMMY = _HASHER.hash("unavailable-account-timing-padding")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class SessionView(BaseModel):
    operator_id: str
    role: str
    csrf_token: str


def _verify(encoded: str, password: str) -> bool:
    try:
        return encoded.startswith("$argon2id$") and _HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


async def _throttled(session: AsyncSession, account: str, ip: str, now: datetime) -> bool:
    secret = get_settings().session_secret
    if secret is None:
        raise HTTPException(503, "operator sessions unavailable")
    blocked = False
    for label, value in (("account", account), ("ip", ip)):
        key = hmac.new(
            secret.get_secret_value().encode(), f"{label}:{value}".encode(), "sha256"
        ).hexdigest()
        await session.execute(
            insert(AuthAttempt)
            .values(bucket_key=key, window_started=now, attempts=0)
            .on_conflict_do_nothing()
        )
        row = await session.get(AuthAttempt, key, with_for_update=True, populate_existing=True)
        if utc(row.window_started) + timedelta(minutes=1) <= now:
            row.window_started, row.attempts = now, 0
        row.attempts += 1
        blocked |= row.attempts > 5
    return blocked


@router.post("/login", response_model=SessionView)
async def login(body: LoginRequest, request: Request, response: Response, session: Session):
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "cross-site login refused")
    now = datetime.now(UTC)
    if await _throttled(
        session, body.operator_id, request.client.host if request.client else "unknown", now
    ):
        await session.commit()
        raise HTTPException(429, "login throttled")
    operator = await session.get(Operator, body.operator_id, with_for_update=True)
    valid = await asyncio.to_thread(
        _verify, operator.password_hash if operator else _DUMMY, body.password
    )
    if (
        operator is None
        or operator.disabled
        or (operator.locked_until and utc(operator.locked_until) > now)
        or not valid
    ):
        if operator is not None and not (
            operator.locked_until and utc(operator.locked_until) > now
        ):
            if operator.locked_until:  # expired lock: start a fresh count, not a 1-strike relock
                operator.failed_logins, operator.locked_until = 0, None
            operator.failed_logins += 1
            if operator.failed_logins >= 5:
                operator.locked_until = now + timedelta(minutes=15)
        await session.commit()
        raise HTTPException(401, "invalid credentials")
    operator.failed_logins, operator.locked_until = 0, None
    previous = request.cookies.get(COOKIE)
    if previous:
        old = await session.get(OperatorSession, token_hash(previous))
        if old:
            await session.delete(old)
    token = secrets.token_urlsafe(32)
    session.add(
        OperatorSession(
            token_hash=token_hash(token),
            operator_id=operator.operator_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + ABSOLUTE_TTL,
        )
    )
    await session.commit()
    response.set_cookie(
        COOKIE, token, secure=True, httponly=True, samesite="strict", max_age=8 * 3600, path="/"
    )
    response.headers["Cache-Control"] = "no-store"
    return SessionView(
        operator_id=operator.operator_id, role=operator.role, csrf_token=csrf_token(token)
    )


@router.get("/me", response_model=SessionView)
async def me(
    request: Request, response: Response, operator: Annotated[Operator, Depends(require_operator)]
):
    response.headers["Cache-Control"] = "no-store"
    return SessionView(
        operator_id=operator.operator_id,
        role=operator.role,
        csrf_token=csrf_token(request.cookies[COOKIE]),
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: Session,
    operator: Annotated[Operator, Depends(require_csrf)],
):
    row = await session.get(OperatorSession, token_hash(request.cookies[COOKIE]))
    if row:
        await session.delete(row)
    await session.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
