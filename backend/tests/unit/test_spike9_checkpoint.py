"""Offline checks for the spike entry point; no LangGraph install or database required."""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPIKES = Path(__file__).resolve().parents[2] / "scripts" / "spikes"
sys.path.insert(0, str(SPIKES))

from common import nonlocal_pg_target  # noqa: E402


def load_function(name, namespace):
    """Execute only the actual CLI function, with network/dependency boundaries replaced."""
    tree = ast.parse((SPIKES / "spike9_checkpoint.py").read_text(encoding="utf-8"))
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SPIKES), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize(
    "params,environ",
    [
        ({"host": "localhost", "hostaddr": "203.0.113.42"}, {}),
        ({"host": "localhost"}, {"PGHOSTADDR": "203.0.113.42"}),
        ({}, {"PGHOST": "db.example.com"}),
        ({"host": "localhost", "service": "prod"}, {}),
        ({"host": "localhost"}, {"PGSERVICE": "prod"}),
        ({"host": "localhost"}, {"PGSERVICEFILE": "unopened-service-file"}),
        ({"host": "localhost,203.0.113.42"}, {}),
    ],
)
def test_cli_rejects_redirects_before_orchestrate(params, environ):
    def no_database():
        pytest.fail("guard allowed a database connection")

    main = load_function(
        "main",
        {
            "DSN": "synthetic conninfo",
            "os": SimpleNamespace(environ=environ),
            "sys": sys,
            "conninfo_to_dict": lambda _: params,
            "nonlocal_pg_target": nonlocal_pg_target,
            "orchestrate": no_database,
        },
    )
    with pytest.raises(SystemExit, match="refusing"):
        main()


@pytest.mark.parametrize("payload", [{"approval_id": "appr_spike9"}, "unknown", None, 42])
async def test_review_rejects_invalid_payload_before_acceptance(payload):
    effects = []

    async def effect(*args):
        effects.append(args)

    review = load_function(
        "review",
        {
            "State": dict,
            "effect": effect,
            "interrupt": lambda _: payload,
            "APPROVAL_ID": "appr_spike9",
        },
    )
    with pytest.raises(ValueError, match="approval_id"):
        await review({"execution_id": "case-1"})
    assert effects == [("case-1", "review", "before_interrupt")]


async def test_valid_resume_returns_acceptance_receipt():
    async def effect(*args):
        pass

    review = load_function(
        "review",
        {
            "State": dict,
            "effect": effect,
            "interrupt": lambda _: "appr_spike9",
            "APPROVAL_ID": "appr_spike9",
        },
    )
    assert await review({"execution_id": "case-1"}) == {
        "approval_id": "appr_spike9",
        "resume_receipts": {"appr_spike9": "case-1"},
    }
