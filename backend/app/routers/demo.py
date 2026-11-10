"""Demo controls and replay scenarios, gated by mode, role and session CSRF (R24–R25)."""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE, roles, token_hash
from app.config import get_settings
from app.db import get_session
from app.demo.runtime import DemoUnavailable, admit, control
from app.models.core import Operator
from app.routers.documents import DocumentReceipt


def enabled():
    if not get_settings().demo_enabled:
        raise HTTPException(404, "Not found")


router = APIRouter(prefix="/api/demo", tags=["demo"], dependencies=[Depends(enabled)])
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


@router.post("/reset", response_model=DemoControlView)
async def reset(
    request: Request,
    session: Session,
    actor: Annotated[Operator, Depends(roles("admin"))],
):
    replay_settings()
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
