"""Authenticated A7 widget selection followed by deterministic read-only execution queries."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analyst import AnalystUnavailable, select_widget
from app.auth import bucket_hit, roles, token_hash
from app.db import get_session
from app.models.core import Operator, VendorMaster
from app.models.payables import InvoiceExecution
from app.schemas.case import ExecutionRow, ExecutionStatus

router = APIRouter(prefix="/api/analyst", tags=["analyst"])


class AnalystRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=1000)


class AnalystCount(BaseModel):
    status: ExecutionStatus
    count: int = Field(ge=0)


class AnalystWidget(BaseModel):
    kind: Literal["status_summary", "execution_list"]
    status: ExecutionStatus | None
    days: Literal[1, 7, 30]
    as_of: datetime
    rows: list[ExecutionRow]
    counts: list[AnalystCount]
    truncated: bool
    replayed: bool


@router.post("/widgets", response_model=AnalystWidget)
async def widget(
    body: AnalystRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[Operator, Depends(roles("controller", "cfo"))],
):
    complete = getattr(request.app.state, "analyst_complete", None)
    if complete is None:
        raise HTTPException(503, "Treasury Analyst unavailable")
    key = "analyst:" + token_hash(actor.operator_id)
    throttled = await bucket_hit(session, key, datetime.now(UTC)) > 5
    await session.commit()  # no transaction or row lock spans provider HTTP
    if throttled:
        raise HTTPException(429, "Treasury Analyst throttled")
    try:
        selected = await asyncio.to_thread(select_widget, body.question, complete=complete)
    except AnalystUnavailable:
        raise HTTPException(
            422, "Question unavailable; use status counts or invoice lists for 1, 7 or 30 days"
        ) from None
    now = datetime.now(UTC)
    filters = [
        InvoiceExecution.created_at >= now - timedelta(days=selected.days),
        InvoiceExecution.created_at <= now,
    ]
    if selected.status is not None:
        filters.append(InvoiceExecution.status == selected.status)
    counts = (
        await session.execute(
            select(InvoiceExecution.status, func.count())
            .where(*filters)
            .group_by(InvoiceExecution.status)
            .order_by(InvoiceExecution.status)
        )
    ).all()
    # Read models only. The model's output never supplies query text, field names or money.
    result = (
        await session.execute(
            select(InvoiceExecution, VendorMaster.vendor_name)
            .outerjoin(VendorMaster, VendorMaster.vendor_id == InvoiceExecution.vendor_id)
            .where(*filters)
            .order_by(InvoiceExecution.created_at.desc(), InvoiceExecution.execution_id)
            .limit(201)
        )
    ).all()
    replayed = bool(
        await session.scalar(
            select(func.count())
            .select_from(InvoiceExecution)
            .where(*filters, InvoiceExecution.replayed)
        )
    )
    return AnalystWidget(
        kind=selected.kind,
        status=selected.status,
        days=selected.days,
        as_of=now,
        rows=[
            ExecutionRow(
                execution_id=row.execution_id,
                status=row.status,
                vendor_name=name,
                billed_cents=row.billed_cents,
                payable_cents=row.approved_cents,
                risk_score=row.risk_score,
                replayed=row.replayed,
            )
            for row, name in result[:200]
        ],
        counts=[AnalystCount(status=status, count=count) for status, count in counts],
        truncated=len(result) > 200,
        replayed=replayed,
    )
