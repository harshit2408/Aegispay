"""Versioned mandate authoring and independent activation (R13)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, roles
from app.config import get_settings
from app.db import get_session
from app.models.core import MandatePolicy, Operator
from app.models.payables import SpendLedger
from app.ops.alerts import enqueue_alert
from app.schemas.decision import MandatePolicy as Policy

router = APIRouter(prefix="/api/mandates", tags=["mandates"])
Session = Annotated[AsyncSession, Depends(get_session)]


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)


class MandateRecord(BaseModel):
    policy_id: str
    version: int
    created_by: str
    is_active: bool
    activated_at: datetime | None
    policy: Policy
    clamped_fields: list[str]
    diff: dict[str, dict[str, Any]]


class ActiveMandateView(BaseModel):
    policy_id: str
    version: int
    policy: Policy
    committed_today_cents: int


def record(row, active=None):
    before = active.compiled_json if active else {}
    after = row.compiled_json
    return MandateRecord(
        policy_id=row.policy_id,
        version=row.version,
        created_by=row.created_by,
        is_active=row.is_active,
        activated_at=row.activated_at,
        policy=Policy.model_validate(after),
        clamped_fields=row.clamped_fields,
        diff={
            key: {"before": before.get(key), "after": after.get(key)}
            for key in sorted(before.keys() | after.keys())
            if before.get(key) != after.get(key)
        },
    )


async def active_policy(session):
    return await session.scalar(select(MandatePolicy).where(MandatePolicy.is_active))


@router.get("/active", response_model=ActiveMandateView)
async def active(session: Session, actor: Annotated[Operator, Depends(require_operator)]):
    row = await active_policy(session)
    if row is None:
        raise HTTPException(404, "no active mandate")
    today = datetime.now(ZoneInfo(row.cap_timezone)).date()
    committed = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (SpendLedger.kind == "RESERVE", SpendLedger.amount_cents),
                        else_=-SpendLedger.amount_cents,
                    )
                ),
                0,
            )
        ).where(SpendLedger.policy_id == row.policy_id, SpendLedger.business_date == today)
    )
    return ActiveMandateView(
        policy_id=row.policy_id,
        version=row.version,
        policy=Policy.model_validate(row.compiled_json),
        committed_today_cents=committed,
    )


@router.get("", response_model=list[MandateRecord])
async def list_mandates(session: Session, actor: Annotated[Operator, Depends(require_operator)]):
    current = await active_policy(session)
    rows = (
        await session.scalars(
            select(MandatePolicy).order_by(MandatePolicy.created_at.desc()).limit(100)
        )
    ).all()
    return [record(row, current) for row in rows]


@router.post("/compile", response_model=MandateRecord, status_code=201)
async def compile_policy(
    body: CompileRequest,
    request: Request,
    session: Session,
    actor: Annotated[Operator, Depends(roles("controller"))],
):
    from app.agents.mandate_compiler import HardLimits, MandateCompileError, compile_mandate

    complete = getattr(request.app.state, "mandate_complete", None)
    if complete is None:
        raise HTTPException(503, "mandate compiler unavailable")
    current = await active_policy(session)
    policy_id = current.policy_id if current else str(uuid4())
    version = (
        await session.scalar(
            select(func.max(MandatePolicy.version)).where(MandatePolicy.policy_id == policy_id)
        )
        or 0
    ) + 1
    active_snapshot = Policy.model_validate(current.compiled_json) if current else None
    author_id = actor.operator_id
    await session.rollback()  # no transaction spans a provider call
    try:
        result = await asyncio.to_thread(
            compile_mandate,
            body.text,
            policy_id=policy_id,
            version=version,
            active=active_snapshot,
            complete=complete,
            limits=HardLimits.from_settings(get_settings()),
        )
    except MandateCompileError:
        raise HTTPException(422, "mandate compilation failed") from None
    policy = result["policy"]
    # Drafts and activation share one lock; final versions are assigned after compilation.
    await session.execute(text("SELECT pg_advisory_xact_lock(71013)"))
    version = (
        await session.scalar(
            select(func.max(MandatePolicy.version)).where(MandatePolicy.policy_id == policy_id)
        )
        or 0
    ) + 1
    policy = policy.model_copy(update={"version": version})
    fields = policy.model_dump(mode="json")
    row = MandatePolicy(
        policy_id=policy_id,
        version=version,
        raw_nl_prompt=body.text,
        compiled_json=fields,
        clamped_fields=result["clamped_fields"],
        created_by=author_id,
        **{
            key: fields[key]
            for key in (
                "max_single_invoice_cents",
                "daily_cap_cents",
                "cap_timezone",
                "max_price_variance_bps",
                "auto_short_pay_enabled",
                "max_short_pay_delta_cents",
                "dual_approval_risk_threshold",
            )
        },
    )
    session.add(row)
    await session.flush()
    output = record(row, await active_policy(session))
    await session.commit()
    return output


@router.post("/{policy_id}/versions/{version}/activate", response_model=MandateRecord)
async def activate(
    policy_id: str,
    version: int,
    body: ActivationRequest,
    session: Session,
    actor: Annotated[Operator, Depends(roles("cfo"))],
):
    await session.execute(text("SELECT pg_advisory_xact_lock(71013)"))
    row = await session.get(MandatePolicy, (policy_id, version), with_for_update=True)
    if row is None:
        raise HTTPException(404, "mandate draft not found")
    if row.created_by == actor.operator_id or row.activated_at is not None:
        raise HTTPException(409, "draft requires independent activation")
    current = await active_policy(session)
    now = datetime.now(UTC)
    if current and current.cap_timezone != row.cap_timezone:
        zone = ZoneInfo(current.cap_timezone)
        next_day = row.created_at.astimezone(zone).date() + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        if now.astimezone(zone).date() < next_day:
            raise HTTPException(409, "timezone change becomes eligible next business day")
    if current:
        current.is_active = False
        await session.flush()
    row.is_active, row.activated_by, row.activated_at = True, actor.operator_id, now
    await enqueue_alert(session, "MANDATE_ACTIVATED")
    output = record(row, current)
    await session.commit()
    return output
