"""Authenticated, bounded Postgres status notifications with a minimal SSE payload."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from psycopg import AsyncConnection
from pydantic import ValidationError

from app.auth import COOKIE, IDLE_TTL, require_operator, token_hash, utc
from app.models.auth import OperatorSession
from app.models.core import Operator
from app.schemas.case import StreamEvent


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


router = APIRouter(prefix="/api", tags=["stream"], dependencies=[Depends(require_operator)])


@router.get(
    "/stream",
    response_class=EventStreamResponse,
    operation_id="execution_stream",
    responses={200: {"model": StreamEvent, "description": "Each data field is a StreamEvent"}},
)
async def execution_stream(request: Request):
    state = request.app.state
    token = token_hash(request.cookies[COOKIE])

    async def events():
        # Short leases force reconnects after identity changes and bound idle DB connections.
        async with await AsyncConnection.connect(state.runtime_dsn, autocommit=True) as conn:
            await conn.execute("LISTEN aegis_status")
            yield ": connected\n\n"
            async for notification in conn.notifies(timeout=20):
                async with state.sessions() as session:
                    login = await session.get(OperatorSession, token)
                    operator = await session.get(Operator, login.operator_id) if login else None
                now = datetime.now(UTC)
                if (
                    not operator
                    or operator.disabled
                    or utc(login.expires_at) <= now
                    or utc(login.last_seen_at) + IDLE_TTL <= now
                ):
                    return
                try:
                    event = StreamEvent.model_validate_json(notification.payload)
                except ValidationError:
                    continue
                yield "data: " + event.model_dump_json() + "\n\n"

    return EventStreamResponse(
        events(), headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )
