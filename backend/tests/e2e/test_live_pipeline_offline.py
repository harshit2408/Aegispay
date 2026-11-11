"""P1-12 offline proof: the real S1 CLI -> real HTTP server -> real sandboxed PDF parse -> match ->
risk -> policy gate -> APPROVED_FOR_PAYMENT on Postgres, with no PayPal and no LLM.

Only two edges are stubbed: the Vendor invoice read (no network) and the model completions (canned
JSON). Everything between them is production code. Settlement and PayPal are NOT exercised here;
the case therefore stops at APPROVED_FOR_PAYMENT and the CLI must say S1 is not proven.
"""

import asyncio
import importlib.util
import json
import platform
import socket
import sys
from argparse import Namespace
from functools import partial
from pathlib import Path

import pytest
import uvicorn
from argon2 import PasswordHasher
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from tokenops.providers.types import ModelResponse

from app.agents.provider import postgres_dsn
from app.config import Settings, get_settings
from app.db import get_session
from app.decision import analyze as analyze_module
from app.decision.analyze import build_analyzer
from app.decision.approve import approve_for_payment
from app.decision.dispatch import Dispatcher
from app.decision.graph import build_graph
from app.demo.seeder import OPERATORS, DemoSeed, seed_demo
from app.models.payables import InvoiceExecution, Payout, POLedger, SettlementOutbox
from app.paypal.invoicing import Invoice, InvoiceItem
from app.routers import auth, contracts, documents
from app.settlement.po_ledger import reserve
from app.settlement.transitions import apply

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PAYER_ID = "PAYERID123456"
VENDOR_EMAIL = "supplier@aegispay-demo.example"
RUN = "s1a"
PASSWORD = "offline-s1-operator-password"
linux_x86 = pytest.mark.skipif(
    sys.platform != "linux"
    or platform.machine() not in ("x86_64", "AMD64")
    or not Path("/usr/bin/tesseract").exists(),  # the detector-on parser path shells out to it
    reason="PDF parser sandbox needs Linux x86_64 with /usr/bin/tesseract",
)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubVendorReader:
    """Read-only stand-in for the Vendor app: the SENT supplier invoice from PRD §15 S1."""

    def __init__(self):
        self.calls = []

    async def get_invoice(self, invoice_id, *, execution_id=None):
        self.calls.append(invoice_id)
        return Invoice(
            invoice_id=invoice_id,
            status="SENT",
            invoice_number=f"AP-1042-{RUN}",
            reference=f"PO-1042-{RUN}",
            currency="USD",
            invoicer_email=VENDOR_EMAIL,
            total_cents=48000,
            paid_cents=0,
            due_cents=48000,
            items=(InvoiceItem(quantity=100, unit_cents=480),),
            issues=(),
        )


@pytest.fixture
async def live_stack(pg_url, pg_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_REPLAY_MODE", "false")
    monkeypatch.setenv("SESSION_SECRET", "synthetic-offline-session-secret-key-1")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "docs"))
    get_settings.cache_clear()
    settings = get_settings()
    await seed_demo(
        pg_sessionmaker,
        config=DemoSeed(
            run_id=RUN,
            vendor_payer_id=PAYER_ID,
            vendor_email=VENDOR_EMAIL,
            treasury_email="buyer@aegispay-demo.example",
            onboarding_evidence={"method": "synthetic offline", "reference": "local fixture"},
            operator_hashes={name: PasswordHasher().hash(PASSWORD) for name in OPERATORS},
        ),
        settings=Settings(database_url=pg_url, demo_enabled=True, demo_replay_mode=False),
        manifest_dir=tmp_path / "manifest",
    )
    prompts = []

    def canned(run_id, *, settings, action):
        def complete(node, messages, *, input_token_bound):
            prompts.append((node, json.dumps(messages)))
            if node == "A2":
                body = {
                    "vendor_invoice_number": f"AP-1042-{RUN}",
                    "po_reference": f"PO-1042-{RUN}",
                    "payee_email": VENDOR_EMAIL,
                    "billed_qty": 100,
                    "unit_price_cents": 480,
                    "total_cents": 48000,
                    "currency": "USD",
                    "field_confidence": {
                        "payee_email": 0.99,
                        "total_cents": 0.99,
                        "billed_qty": 0.99,
                    },
                }
            else:
                body = {"label": "CLEAN", "rationale": "no instructions in the document"}
            return ModelResponse(content=json.dumps(body))

        return action(complete)

    monkeypatch.setattr(analyze_module, "governed_analysis", canned)

    reader = StubVendorReader()
    app = FastAPI()
    for router in (auth.router, documents.router, contracts.router):
        app.include_router(router)

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    gate_settings = {"vendor_new_days": 7, "max_vendor_invoices": 5}
    async with AsyncPostgresSaver.from_conn_string(postgres_dsn(pg_url)) as saver:
        await saver.setup()
        graph = build_graph(
            saver,
            sessions=pg_sessionmaker,
            analyze=build_analyzer(pg_sessionmaker, settings),
            reader=reader,
            replay_reader=None,
            transition=apply,
            approve=partial(approve_for_payment, pg_sessionmaker, apply, reserve, **gate_settings),
            **gate_settings,
        )
        dispatcher = Dispatcher(graph, pg_sessionmaker, postgres_dsn(pg_url))
        app.state.dispatcher = dispatcher
        app.state.run_case = dispatcher.run
        app.state.vendor_reader = reader
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
        )
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", reader, prompts
        finally:
            server.should_exit = True
            await task
            get_settings.cache_clear()


def cli_args(tmp_path, pdf, out_name):
    return Namespace(
        pdf=str(pdf),
        po=f"PO-1042-{RUN}",
        invoice="INV2-OFFLINE",
        out=str(tmp_path / out_name),
        timeout=6,
        grace=0,
    )


@linux_x86
async def test_cli_upload_reaches_approved_for_payment_and_is_not_s1_pass(
    live_stack, pg_sessionmaker, tmp_path, monkeypatch, capsys
):
    base, reader, prompts = live_stack
    headless, make_pdf = _load("s1_headless"), _load("s1_make_pdf")
    pdf = tmp_path / "S1.pdf"
    make_pdf.build(pdf, run=RUN, payee_email=VENDOR_EMAIL)
    monkeypatch.setenv("AEGIS_API_URL", base)
    monkeypatch.setenv("AEGIS_OPERATOR_ID", "demo-controller1")
    monkeypatch.setenv("AEGIS_OPERATOR_PASSWORD", PASSWORD)

    code = await asyncio.to_thread(headless.run, cli_args(tmp_path, pdf, "s1.json"))

    evidence = json.loads((tmp_path / "s1.json").read_text())
    case = evidence["case"]
    assert case["status"] == "APPROVED_FOR_PAYMENT", (case["gate"], case["match"], case["risk"])
    assert code == 1  # nothing settled: S1 must NOT pass offline
    assert "status is APPROVED_FOR_PAYMENT, expected SETTLED" in evidence["problems"]
    assert evidence["case"]["uploaded_by"] == "demo-controller1"  # R11: actor from the session
    # Real sandboxed parse fed the (stubbed) model: the PDF text reached A2 and A3.
    assert {node for node, _ in prompts} == {"A2", "A3"}
    assert all(f"AP-1042-{RUN}" in text and "480.00" in text for _, text in prompts)
    assert reader.calls == ["INV2-OFFLINE"] * 2  # upload admission + match, read-only
    async with pg_sessionmaker() as session:
        row = await session.get(InvoiceExecution, evidence["execution_id"])
        assert (row.status, row.route, row.approved_cents, row.billed_cents) == (
            "APPROVED_FOR_PAYMENT",
            "AUTO_SETTLE",
            48000,
            48000,
        )
        outbox = await session.scalar(select(SettlementOutbox))
        assert (outbox.amount_cents, outbox.receiver_payer_id) == (48000, PAYER_ID)  # R1, R3
        reserved = await session.scalar(select(POLedger))
        assert (reserved.kind, reserved.amount_cents) == ("RESERVE", 48000)
        assert await session.scalar(select(Payout)) is None  # no payout without settlement
    assert evidence["case"]["gate"]["route"] == "AUTO_SETTLE"
    assert evidence["case"]["settlement_timeline"] == []
    assert evidence["case"]["audit_anchor"]["verified"] is False

    # S4: the same invoice again is blocked at admission and the CLI refuses to call it S1.
    code = await asyncio.to_thread(headless.run, cli_args(tmp_path, pdf, "dup.json"))
    duplicate = json.loads((tmp_path / "dup.json").read_text())
    assert code == 1 and "case is a duplicate" in duplicate["problems"]
    assert duplicate["case"]["status"] == "DUPLICATE_BLOCKED"
    assert "S1 PASS" not in capsys.readouterr().out
