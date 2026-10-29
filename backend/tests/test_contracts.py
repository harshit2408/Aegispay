"""The P1 API seam must publish types without enabling decisions or money movement."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.case import DecisionRequest, StreamEvent
from app.schemas.decision import GateDecision


@pytest.mark.parametrize("extra", [{"actor": "forged"}, {"payee": "forged"}, {"amount_cents": 1}])
def test_decision_request_never_accepts_actor_payee_or_amount(extra):
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate(
            {
                "action": "APPROVE",
                "reason": "Reviewed current evidence",
                "gate_hash": "a" * 64,
                **extra,
            }
        )


@pytest.mark.parametrize("reason", ["short", "          "])
def test_decision_reason_is_meaningful_after_trimming(reason):
    with pytest.raises(ValidationError):
        DecisionRequest(action="APPROVE", reason=reason, gate_hash="a" * 64)


@pytest.mark.parametrize("amount", [-1, 1.5, True, "42500"])
def test_gate_rejects_invalid_or_coerced_cents(amount):
    with pytest.raises(ValidationError):
        GateDecision(
            route="FREEZE",
            payable_cents=amount,
            reasons=["HIGH_RISK"],
            required_approvals=2,
            allowed_actions=["APPROVE"],
            policy_id="test",
            policy_version=1,
            gate_hash="a" * 64,
        )


async def test_published_case_routes_fail_closed_without_services_or_auth():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ["/api/executions", "/api/executions/example", "/api/stream"]:
            response = await client.get(path)
            assert response.status_code == 501
            assert response.headers["content-type"].startswith("application/json")
        response = await client.post(
            "/api/executions/example/decisions",
            json={
                "action": "APPROVE",
                "reason": "Reviewed current evidence",
                "gate_hash": "a" * 64,
            },
        )
        assert response.status_code == 501
        assert "not implemented" in response.json()["detail"]
        response = await client.post(
            "/api/executions/example/decisions",
            json={
                "action": "APPROVE",
                "reason": "Reviewed current evidence",
                "gate_hash": "a" * 64,
                "actor": "forged",
            },
        )
        assert response.status_code == 422


def test_committed_openapi_matches_actual_server_contract():
    schema_file = Path(__file__).resolve().parents[1] / "openapi.json"
    assert json.loads(schema_file.read_text(encoding="utf-8")) == app.openapi()
    request = app.openapi()["components"]["schemas"]["DecisionRequest"]
    assert set(request["properties"]) == {"action", "reason", "gate_hash"}
    assert request["additionalProperties"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"payload": "invoice text"},
        {"actor": "someone"},
        {"status": "evidence with spaces"},
        {"ts": "2026-10-28"},
    ],
)
def test_stream_contract_rejects_evidence_fields_and_invalid_state(change):
    event = {
        "type": "node",
        "execution_id": "test-id",
        "name": "match",
        "status": "completed",
        "ts": "2026-10-28T06:30:00Z",
        **change,
    }
    with pytest.raises(ValidationError):
        StreamEvent.model_validate_json(json.dumps(event))


def test_stream_contract_is_minimal_and_generated_as_sse():
    event = StreamEvent.model_validate_json(
        json.dumps(
            {
                "type": "node",
                "execution_id": "test-id",
                "name": "match",
                "status": "completed",
                "ts": "2026-10-28T06:30:00Z",
            }
        )
    )
    assert set(event.model_dump()) == {"type", "execution_id", "name", "status", "ts"}
    response = app.openapi()["paths"]["/api/stream"]["get"]["responses"]["200"]
    assert response["content"]["text/event-stream"]["schema"]["$ref"].endswith("/StreamEvent")
    error = app.openapi()["paths"]["/api/stream"]["get"]["responses"]["501"]
    assert set(error["content"]) == {"application/json"}
