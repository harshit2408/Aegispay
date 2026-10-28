"""P1 frontend contract seam; no case service or authentication is implemented here."""

from fastapi import APIRouter, HTTPException

from app.schemas.case import APIError, CaseDetail, DecisionReceipt, DecisionRequest, ExecutionRow

router = APIRouter(
    prefix="/api/executions",
    tags=["P1 contract (not implemented)"],
    responses={501: {"model": APIError, "description": "Case service/auth not implemented"}},
)


@router.get("", response_model=list[ExecutionRow], operation_id="list_executions")
async def list_executions():
    raise HTTPException(501, "Case service and operator authentication are not implemented")


@router.get("/{execution_id}", response_model=CaseDetail, operation_id="get_execution")
async def get_execution(execution_id: str):
    raise HTTPException(501, "Case service and operator authentication are not implemented")


@router.post(
    "/{execution_id}/decisions",
    response_model=DecisionReceipt,
    operation_id="submit_decision",
    responses={409: {"model": APIError, "description": "Case changed; review again"}},
)
async def submit_decision(execution_id: str, decision: DecisionRequest):
    # Fail closed before an approval, graph resume, reservation or payment can exist.
    raise HTTPException(
        501, "Decision service, operator session and CSRF checks are not implemented"
    )
