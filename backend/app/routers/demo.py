"""Demo controls and replay scenarios, gated by mode, role and session CSRF (R24–R25)."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE, roles, token_hash
from app.config import get_settings
from app.db import get_session
from app.demo import runtime
from app.demo.runtime import DemoUnavailable, admit, control
from app.models.core import Operator
from app.routers.documents import DocumentReceipt


def enabled():
    if not get_settings().demo_enabled:
        raise HTTPException(404, "Not found")


router = APIRouter(prefix="/api/demo", tags=["demo"], dependencies=[Depends(enabled)])
WAITERS = asyncio.Semaphore(4)  # bounds concurrent live-reset waits per API process
Session = Annotated[AsyncSession, Depends(get_session)]


class DetectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class DemoControlView(BaseModel):
    run_id: str
    detector_enabled: bool
    previous_run_id: str | None
    replayed: bool


def replay_settings():
    settings = get_settings()
    if not settings.demo_replay_mode:
        raise HTTPException(503, "Live demo reset requires the owner sandbox seeder")
    return settings


def unavailable(error):
    code = 429 if "throttled" in str(error) else 409
    return HTTPException(code, str(error))


async def live_reset(request: Request, session: AsyncSession, actor: Operator):
    """Queue a reset for the owner runner and wait for it; the API never seeds trust roots."""
    if WAITERS.locked():
        raise HTTPException(429, "Too many reset requests are already waiting")
    async with WAITERS:
        return await _live_reset(request, session, actor)


async def _live_reset(request: Request, session: AsyncSession, actor: Operator):
    try:
        request_id = await runtime.request_live_reset(
            session, actor.operator_id, bucket=token_hash(request.cookies[COOKIE])
        )
    except DemoUnavailable as error:
        await session.rollback()
        raise unavailable(error) from None
    outcome = await runtime.wait_for_reset(
        session,
        request_id,
        timeout=runtime.LIVE_RESET_WAIT_SECONDS,
        interval=runtime.LIVE_RESET_POLL_SECONDS,
    )
    if outcome is None:
        raise HTTPException(504, "Reset pending: the owner reset runner has not finished")
    kind, payload = outcome
    run_id = payload.get("run_id")
    if (
        kind != runtime.RESET_DONE
        or not isinstance(run_id, str)
        or not runtime.RUN_ID.fullmatch(run_id)
    ):
        raise HTTPException(502, "Reset failed in the owner reset runner")
    previous = payload.get("previous_run_id")
    return DemoControlView(
        run_id=run_id,
        detector_enabled=True,
        previous_run_id=previous if isinstance(previous, str) else None,
        replayed=False,
    )


@router.post("/reset", response_model=DemoControlView)
async def reset(
    request: Request,
    session: Session,
    actor: Annotated[Operator, Depends(roles("admin"))],
):
    if not get_settings().demo_replay_mode:
        return await live_reset(request, session, actor)
    try:
        return await control(session, actor.operator_id, bucket=token_hash(request.cookies[COOKIE]))
    except DemoUnavailable as error:
        await session.rollback()
        raise unavailable(error) from None


@router.post("/forensics-detector", response_model=DemoControlView)
async def detector(
    body: DetectorRequest,
    request: Request,
    session: Session,
    actor: Annotated[Operator, Depends(roles("admin"))],
):
    replay_settings()
    try:
        return await control(
            session,
            actor.operator_id,
            detector_enabled=body.enabled,
            bucket=token_hash(request.cookies[COOKIE]),
        )
    except DemoUnavailable as error:
        await session.rollback()
        raise unavailable(error) from None


@router.post("/scenarios/{scenario_id}/run", status_code=202, response_model=DocumentReceipt)
async def run_scenario(
    scenario_id: Literal["S1", "S2", "S3", "S4"],
    request: Request,
    background: BackgroundTasks,
    session: Session,
    actor: Annotated[Operator, Depends(roles("controller"))],
):
    settings = replay_settings()
    run_case = getattr(request.app.state, "run_case", None)
    if run_case is None:
        raise HTTPException(503, "Decision runtime unavailable")
    try:
        execution_id, duplicate = await admit(
            session,
            settings,
            actor=actor.operator_id,
            bucket=token_hash(request.cookies[COOKIE]),
            scenario=scenario_id,
        )
    except DemoUnavailable as error:
        await session.rollback()
        raise unavailable(error) from None
    if duplicate is None:
        background.add_task(run_case, execution_id)
    return DocumentReceipt(execution_id=execution_id, duplicate_of_execution_id=duplicate)
