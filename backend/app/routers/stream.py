"""Publish the R25 stream contract while session checks and fan-out remain P2-13/P3-13."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.case import StreamEvent


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


router = APIRouter(prefix="/api", tags=["Stream contract (not implemented)"])


@router.get(
    "/stream",
    response_class=EventStreamResponse,
    operation_id="execution_stream",
    responses={
        200: {
            "model": StreamEvent,
            "description": "Each SSE data field is a StreamEvent JSON object",
        },
        501: {
            "description": "Authenticated stream not implemented",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/APIError"}}},
        },
    },
)
async def execution_stream():
    raise HTTPException(
        501, "Operator session checks and authenticated event stream are not implemented"
    )
