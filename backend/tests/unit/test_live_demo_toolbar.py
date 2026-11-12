"""P3-09: live (non-replay) demo toolbar: persisted detector toggle and S1–S4 admission (R24)."""

import asyncio
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import make_transient

from app.audit.events import enqueue_event
from app.config import get_settings
from app.db import get_session
from app.decision import analyze as analyze_module
from app.demo import reset_runner, runtime
from app.demo.seeder import seed_demo
from app.models.core import MandatePolicy
from app.models.payables import InvoiceDocument, InvoiceExecution
from app.paypal.invoicing import Invoice, InvoiceItem
from app.routers import auth, contracts, demo, documents
from tests.unit.test_live_reset_health import control_events, login, seed

EMAIL = "supplier@aegispay-demo.example"


class FakeVendor:
    """Owner-side Vendor gateway double; the API only gets its read method (R22)."""

    def __init__(self):
        self.invoices = {}
        self.reads = []

    async def create_draft(self, spec, *, request_id):
        invoice_id = "INV2-" + spec.invoice_number.upper()
        self.invoices[invoice_id] = Invoice(
            invoice_id=invoice_id,
            status="DRAFT",
            invoice_number=spec.invoice_number,
            reference=spec.reference,
            currency="USD",
            invoicer_email=spec.invoicer_email,
            total_cents=spec.quantity * spec.unit_cents,
            paid_cents=0,
            due_cents=spec.quantity * spec.unit_cents,
            items=(InvoiceItem(spec.quantity, spec.unit_cents),),
            issues=(),
        )
        return invoice_id

    async def send(self, invoice_id, *, request_id):
        self.invoices[invoice_id] = replace(self.invoices[invoice_id], status="SENT")

    async def get_invoice(self, invoice_id, *, execution_id=None):
        self.reads.append(invoice_id)
        return self.invoices[invoice_id]


class ReadOnly:
    def __init__(self, vendor):
        self.get_invoice = vendor.get_invoice


@pytest.fixture
async def live(pg_url, pg_sessionmaker, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DEMO_ENABLED", "true")
    monkeypatch.setenv("DEMO_REPLAY_MODE", "false")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SESSION_SECRET", "synthetic-live-toolbar-session-secret")
    monkeypatch.setattr(runtime, "LIVE_RESET_POLL_SECONDS", 0.02)
    monkeypatch.setattr(reset_runner, "POLL_SECONDS", 0.02)
    get_settings.cache_clear()
    settings = get_settings()
    vendor = FakeVendor()
    template = seed("base")
    runs = tmp_path / "runs"
    await seed_demo(
        pg_sessionmaker, config=template, settings=settings, manifest_dir=runs, gateway=vendor
    )
    app = FastAPI()
    for router in (auth.router, contracts.router, demo.router, documents.router):
        app.include_router(router)

    async def sessions():
        async with pg_sessionmaker() as session:
            yield session

    started = []

    async def run_case(execution_id):
        started.append(execution_id)

    app.dependency_overrides[get_session] = sessions
    app.state.dispatcher = object()
    app.state.vendor_reader = ReadOnly(vendor)
    app.state.run_case = run_case
    stop = asyncio.Event()
    runner = asyncio.create_task(
        reset_runner.serve(
            pg_sessionmaker,
            stop=stop,
            template=template,
            settings=settings,
            manifest_dir=runs,
            gateway=vendor,
        )
    )
    try:
        yield app, pg_sessionmaker, vendor, started
    finally:
        stop.set()
        await runner
        get_settings.cache_clear()


async def execution(sessions, execution_id):
    async with sessions() as session:
        return await session.get(InvoiceExecution, execution_id)


async def test_seeder_records_s1_to_s3_invoices_in_the_database(live):
    _, sessions, vendor, _ = live
    assert sorted(vendor.invoices) == [
        "INV2-AP-1042-BASE",
        "INV2-AP-1043-BASE",
        "INV2-AP-1044-BASE",
    ]
    assert all(invoice.status == "SENT" for invoice in vendor.invoices.values())
    [event] = await control_events(sessions, runtime.LIVE_INVOICES)
    assert event.payload == {
        "run_id": "base",
        "invoices": {
            "S1": "INV2-AP-1042-BASE",
            "S2": "INV2-AP-1043-BASE",
            "S3": "INV2-AP-1044-BASE",
        },
    }


async def test_live_detector_persists_per_run_and_reset_restores_on(live):
    app, sessions, _, _ = live
    async with login(app, "admin") as admin:
        state = await admin.get("/api/demo/forensics-detector")
        assert state.status_code == 200, state.text
        assert state.json() == {
            "run_id": "base",
            "detector_enabled": True,
            "previous_run_id": None,
            "replayed": False,
        }
        off = await admin.post("/api/demo/forensics-detector", json={"enabled": False})
        assert off.status_code == 200, off.text
        assert off.json()["detector_enabled"] is False
        assert (await admin.get("/api/demo/forensics-detector")).json()["detector_enabled"] is False
        run = await admin.post("/api/demo/scenarios/S3/run")
        assert run.status_code == 202, run.text
        assert (await execution(sessions, run.json()["execution_id"])).detector_enabled is False
        reset = await admin.post("/api/demo/reset")
        assert reset.status_code == 200, reset.text
        assert reset.json()["detector_enabled"] is True
        after = (await admin.get("/api/demo/forensics-detector")).json()
        assert after["run_id"] == reset.json()["run_id"]
        assert after["detector_enabled"] is True
    assert len(await control_events(sessions, runtime.LIVE_DETECTOR)) == 1


async def test_live_document_upload_honours_the_detector_only_when_demo_enabled(live, monkeypatch):
    app, sessions, _, _ = live
    async with login(app, "admin") as admin:
        assert (
            await admin.post("/api/demo/forensics-detector", json={"enabled": False})
        ).status_code == 200
    form = {"po_id": "PO-1043-base", "paypal_invoice_id": "INV2-AP-1043-BASE"}
    pdf = {"file": ("invoice.pdf", b"%PDF-1.7 synthetic", "application/pdf")}
    async with login(app, "controller1") as controller:
        uploaded = await controller.post("/api/documents", data=form, files=pdf)
        assert uploaded.status_code == 202, uploaded.text
    assert (await execution(sessions, uploaded.json()["execution_id"])).detector_enabled is False

    # R24: without DEMO_ENABLED the recorded toggle is ignored and the detector is always ON.
    monkeypatch.setenv("DEMO_ENABLED", "false")
    get_settings.cache_clear()
    form = {"po_id": "PO-1042-base", "paypal_invoice_id": "INV2-AP-1042-BASE"}
    async with login(app, "controller1") as controller:
        uploaded = await controller.post("/api/documents", data=form, files=pdf)
        assert uploaded.status_code == 202, uploaded.text
    assert (await execution(sessions, uploaded.json()["execution_id"])).detector_enabled is True


async def test_live_s1_to_s4_use_document_admission_and_seeded_invoices(live):
    app, sessions, vendor, started = live
    async with login(app, "admin") as admin:
        early = await admin.post("/api/demo/scenarios/S4/run")
        assert early.status_code == 409
        assert early.json()["detail"] == "Run S1 before the duplicate scenario"
        receipts = {}
        for scenario in ("S1", "S2", "S3", "S4"):
            response = await admin.post(f"/api/demo/scenarios/{scenario}/run")
            assert response.status_code == 202, response.text
            receipts[scenario] = response.json()
    s1 = receipts["S1"]["execution_id"]
    assert receipts["S4"]["duplicate_of_execution_id"] == s1
    assert started == [receipts[name]["execution_id"] for name in ("S1", "S2", "S3")]
    expected = {
        "S1": ("PO-1042-base", "INV2-AP-1042-BASE", "RECEIVED"),
        "S2": ("PO-1043-base", "INV2-AP-1043-BASE", "RECEIVED"),
        "S3": ("PO-1044-base", "INV2-AP-1044-BASE", "RECEIVED"),
        "S4": ("PO-1042-base", "INV2-AP-1042-BASE", "DUPLICATE_BLOCKED"),
    }
    async with sessions() as session:
        for scenario, (po_id, invoice_id, status) in expected.items():
            row = await session.get(InvoiceExecution, receipts[scenario]["execution_id"])
            assert (row.po_id, row.paypal_invoice_id, row.status) == (po_id, invoice_id, status)
            assert row.replayed is False and row.detector_enabled is True
            document = await session.get(InvoiceDocument, row.document_id)
            assert document.source == "SEEDED_INBOX"
            assert document.uploaded_by == "demo-admin"
        documents_by_id = {
            row.document_id: row for row in (await session.scalars(select(InvoiceDocument))).all()
        }
    # S4 re-uploads S1's exact PDF; S3 carries the hidden-text attack.
    s4 = documents_by_id[receipts["S4"]["execution_id"]]
    assert s4.sha256 == documents_by_id[s1].sha256
    s3_pdf = open(documents_by_id[receipts["S3"]["execution_id"]].storage_path, "rb").read()
    assert s3_pdf.startswith(b"%PDF-")
    # Every non-duplicate admission was GET-verified against PayPal; the API wrote nothing to it.
    assert set(vendor.reads) >= {"INV2-AP-1042-BASE", "INV2-AP-1043-BASE", "INV2-AP-1044-BASE"}


async def test_live_scenarios_need_recorded_invoices(live, tmp_path):
    app, sessions, _, _ = live
    # A run seeded without --send-invoices has POs and GRNs but no PayPal invoices.
    await seed_demo(
        sessions, config=seed("bare"), settings=get_settings(), manifest_dir=tmp_path / "bare"
    )
    async with login(app, "admin") as admin:
        response = await admin.post("/api/demo/scenarios/S1/run")
    assert response.status_code == 409
    assert "invoices" in response.json()["detail"]


@pytest.mark.parametrize("name", ["controller1", "cfo", "receiver", "vendor_admin1"])
async def test_only_admin_runs_live_scenarios_or_reads_the_detector(live, name):
    app, sessions, _, _ = live
    async with login(app, name) as operator:
        assert (await operator.post("/api/demo/scenarios/S1/run")).status_code == 403
        assert (await operator.get("/api/demo/forensics-detector")).status_code == 403
    async with sessions() as session:
        assert (await session.scalars(select(InvoiceExecution))).all() == []


async def test_live_scenario_requires_session_and_csrf(live):
    app, *_ = live
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as anonymous:
        assert (await anonymous.post("/api/demo/scenarios/S1/run")).status_code == 401
        assert (await anonymous.get("/api/demo/forensics-detector")).status_code == 401
    async with login(app, "admin") as admin:
        del admin.headers["X-CSRF-Token"]
        response = await admin.post("/api/demo/scenarios/S1/run")
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token required"


PDF = {"file": ("invoice.pdf", b"%PDF-1.7 synthetic", "application/pdf")}
S2_FORM = {"po_id": "PO-1043-base", "paypal_invoice_id": "INV2-AP-1043-BASE"}
VALID = {"S1": "INV2-AP-1042-BASE", "S2": "INV2-AP-1043-BASE", "S3": "INV2-AP-1044-BASE"}


async def forge(sessions, payload, **kwargs):
    """An API-role writer can append control events; admission must still fail closed."""
    async with sessions() as session, session.begin():
        await enqueue_event(
            session,
            execution_id="demo-control",
            actor="forger",
            event_type=runtime.LIVE_INVOICES,
            payload=payload,
            **kwargs,
        )


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"run_id": "base", "invoices": {**VALID, "S1": "bad id!"}}, "invoices unavailable"),
        ({"run_id": "base", "invoices": {"S1": VALID["S1"], "S2": VALID["S2"]}}, "invoices"),
        # S1 pointed at S2's real invoice: the PayPal GET shows a different PO reference.
        ({"run_id": "base", "invoices": {**VALID, "S1": VALID["S2"]}}, "does not match"),
    ],
)
async def test_forged_invoice_mapping_fails_closed(live, payload, detail):
    app, sessions, _, started = live
    await forge(sessions, payload)
    async with login(app, "admin") as admin:
        response = await admin.post("/api/demo/scenarios/S1/run")
    assert response.status_code == 409, response.text
    assert detail in response.json()["detail"]
    assert started == []


async def test_mapping_for_another_run_is_ignored(live, tmp_path):
    app, sessions, _, _ = live
    await seed_demo(
        sessions, config=seed("bare"), settings=get_settings(), manifest_dir=tmp_path / "bare"
    )
    await forge(sessions, {"run_id": "base", "invoices": VALID})  # valid IDs, wrong run
    async with login(app, "admin") as admin:
        response = await admin.post("/api/demo/scenarios/S1/run")
    assert response.status_code == 409
    assert "invoices" in response.json()["detail"]


async def test_newest_mapping_wins_by_sequence_not_timestamp(live):
    app, sessions, _, _ = live
    # Newer row, older caller-supplied ts: it still wins (and is malformed, so 409).
    await forge(
        sessions,
        {"run_id": "base", "invoices": {"S1": "bad id!"}},
        ts=datetime(2000, 1, 1, tzinfo=UTC),
    )
    async with login(app, "admin") as admin:
        assert (await admin.post("/api/demo/scenarios/S1/run")).status_code == 409


async def test_live_scenario_attempts_are_throttled_including_failures(live):
    app, *_ = live
    async with login(app, "admin") as admin:
        for _ in range(10):
            early = await admin.post("/api/demo/scenarios/S4/run")
            assert early.status_code == 409  # S4 before S1 still counts
        assert (await admin.post("/api/demo/scenarios/S1/run")).status_code == 429


async def test_live_detector_toggle_is_throttled(live):
    app, *_ = live
    async with login(app, "admin") as admin:
        for _ in range(10):
            ok = await admin.post("/api/demo/forensics-detector", json={"enabled": False})
            assert ok.status_code == 200
        throttled = await admin.post("/api/demo/forensics-detector", json={"enabled": True})
        assert throttled.status_code == 429
        assert (await admin.get("/api/demo/forensics-detector")).json()["detector_enabled"] is False


async def test_non_demo_mandate_forces_detector_on(live):
    app, sessions, _, _ = live
    async with login(app, "admin") as admin:
        await admin.post("/api/demo/forensics-detector", json={"enabled": False})
    async with sessions() as session, session.begin():
        policy = await session.get(MandatePolicy, ("demo-base", 1))
        await session.execute(update(MandatePolicy).values(is_active=False))
        session.expunge(policy)
        make_transient(policy)
        policy.policy_id = "corp-1"
        policy.is_active = True
        session.add(policy)
    async with login(app, "controller1") as controller:
        uploaded = await controller.post("/api/documents", data=S2_FORM, files=PDF)
        assert uploaded.status_code == 202, uploaded.text
    assert (await execution(sessions, uploaded.json()["execution_id"])).detector_enabled is True


async def test_analysis_forces_detector_on_without_demo(live, monkeypatch):
    app, sessions, _, _ = live
    async with login(app, "admin") as admin:
        await admin.post("/api/demo/forensics-detector", json={"enabled": False})
    async with login(app, "controller1") as controller:
        uploaded = await controller.post("/api/documents", data=S2_FORM, files=PDF)
    execution_id = uploaded.json()["execution_id"]
    assert (await execution(sessions, execution_id)).detector_enabled is False
    seen = []

    class Stop(Exception):
        pass

    async def parse(pdf, *, detector_enabled):
        seen.append(detector_enabled)
        raise Stop

    monkeypatch.setattr(analyze_module, "parse_pdf", parse)
    for demo_enabled in ("true", "false"):
        monkeypatch.setenv("DEMO_ENABLED", demo_enabled)
        get_settings.cache_clear()
        with pytest.raises(Stop):
            await analyze_module.build_analyzer(sessions, get_settings())(execution_id)
    assert seen == [False, True]  # R24: a stored OFF is ignored outside demo deployments


async def test_document_upload_regressions_after_refactor(live, monkeypatch):
    app, sessions, _, _ = live
    monkeypatch.setattr(documents, "MAX_PDF_BYTES", 32)
    big = {"file": ("big.pdf", b"%PDF-" + b"0" * 40, "application/pdf")}
    text = {"file": ("x.pdf", b"not a pdf", "application/pdf")}
    receipts = []
    async with login(app, "controller1") as controller:
        assert (await controller.post("/api/documents", data=S2_FORM, files=big)).status_code == 413
        assert (
            await controller.post("/api/documents", data=S2_FORM, files=text)
        ).status_code == 422
        for _ in range(10):
            response = await controller.post("/api/documents", data=S2_FORM, files=PDF)
            assert response.status_code == 202, response.text
            receipts.append(response.json())
        assert (await controller.post("/api/documents", data=S2_FORM, files=PDF)).status_code == 429
    first = receipts[0]["execution_id"]
    assert all(r["duplicate_of_execution_id"] == first for r in receipts[1:])
    async with sessions() as session:
        document = await session.get(InvoiceDocument, first)
    assert (document.source, document.uploaded_by) == ("PDF_UPLOAD", "demo-controller1")


def test_documents_router_does_not_load_demo_code():
    probe = (
        "import sys, app.routers.documents; "
        "loaded = [m for m in sys.modules if m.startswith('app.demo')]; "
        "assert not loaded, loaded"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)
