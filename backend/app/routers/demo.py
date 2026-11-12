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
from app.demo.pdf import scenario_pdf
from app.demo.runtime import DemoUnavailable, admit, control, latest_control, live_control
from app.demo.seeder import SCENARIOS
from app.models.core import Operator, PurchaseOrder, VendorMaster
from app.routers.documents import (
    DocumentReceipt,
    _duplicate,
    admit_pdf,
    require_live_admission,
)


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


@router.get("/forensics-detector", response_model=DemoControlView)
async def detector_state(
    session: Session,
    _: Annotated[Operator, Depends(roles("admin"))],
):
    """Current kill-switch state for newly admitted scenarios (PRD §14.5)."""
    try:
        if get_settings().demo_replay_mode:
            state = await latest_control(session)
            await session.rollback()
            return DemoControlView(**state, previous_run_id=None, replayed=True)
        return await live_control(session)
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
    toggle = control if get_settings().demo_replay_mode else live_control
    try:
        return await toggle(
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
    # PRD §14.5 admin-only toolbar; admin cannot approve (R18), so the uploader never votes.
    actor: Annotated[Operator, Depends(roles("admin"))],
):
    settings = get_settings()
    if not settings.demo_replay_mode:
        return await live_scenario(request, background, session, actor, scenario_id)
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


async def live_scenario(request, background, session, actor, scenario):
    """Feed the run's seeded PDF and PayPal invoice through /api/documents admission.

    S3 is the hidden-text attack PDF; S4 re-uploads S1's PDF with S1's invoice. The invoice IDs
    were created by the owner seeder; this process only GET-verifies them (R22).
    """
    require_live_admission(request)
    actor_id = actor.operator_id  # read before rollbacks expire the ORM row
    source = "S1" if scenario == "S4" else scenario
    try:
        run_id = (await runtime.seeded_policy(session)).policy_id.removeprefix("demo-")
        invoice_id = (await runtime.live_invoices(session, run_id))[source]
    except DemoUnavailable as error:
        await session.rollback()
        raise unavailable(error) from None
    number = next(row[1] for row in SCENARIOS if row[0] == source)
    po_id = f"PO-{number}-{run_id}"
    po = await session.get(PurchaseOrder, po_id)
    vendor = await session.get(VendorMaster, po.vendor_id) if po else None
    if vendor is None:
        await session.rollback()
        raise HTTPException(409, "Seeded purchase order or vendor unavailable")
    if scenario == "S4" and await _duplicate(session, invoice_id, vendor.vendor_id) is None:
        await session.rollback()
        raise HTTPException(409, "Run S1 before the duplicate scenario")
    payee = vendor.verified_paypal_email
    await session.rollback()
    pdf = await asyncio.to_thread(scenario_pdf, run=run_id, payee_email=payee, scenario=source)
    return await admit_pdf(
        request,
        background,
        session,
        actor_id,
        pdf,
        po_id,
        invoice_id,
        source="SEEDED_INBOX",
        note={"scenario": scenario, "run_id": run_id},
    )
