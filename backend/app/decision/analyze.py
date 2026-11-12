"""Produce A2 and A3 evidence inside one governed invoice run (PRD §§10–11)."""

import asyncio
import base64
import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from app.agents.extractor import FREEZE_REASON, ExtractionResult, extract_invoice
from app.agents.provider import governed_analysis
from app.forensics.classifier import classify
from app.models.payables import InvoiceDocument, InvoiceExecution
from app.parser import MAX_BYTES, ParseUnavailable, parse_pdf
from app.schemas.decision import ForensicsReport, InvoiceExtraction

DETECTOR_VERSION = "pdf-structure+ocr-diff-v1"


def _read_pdf(path: str, expected_hash: str) -> bytes:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected_hash:
        raise ParseUnavailable("stored PDF failed size or integrity check")
    return data


def _budget_id(execution_id: str) -> UUID:
    try:
        return UUID(execution_id)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"aegis:execution:{execution_id}")


def build_analyzer(sessions, settings):
    async def analyze(execution_id: str) -> tuple[ExtractionResult, ForensicsReport]:
        async with sessions() as session:
            document, detector_enabled, replayed = (
                await session.execute(
                    select(
                        InvoiceDocument,
                        InvoiceExecution.detector_enabled,
                        InvoiceExecution.replayed,
                    )
                    .join(
                        InvoiceExecution,
                        InvoiceExecution.document_id == InvoiceDocument.document_id,
                    )
                    .where(InvoiceExecution.execution_id == execution_id)
                )
            ).one()
        # R24: the detector can be OFF only in a demo deployment, whatever the row says.
        detector_enabled = detector_enabled if settings.demo_enabled else True
        if document.forensics_json is not None:
            if document.extraction_json is None:
                extraction = ExtractionResult(extraction=None, freeze_reason=FREEZE_REASON)
            else:
                extraction = ExtractionResult(
                    extraction=InvoiceExtraction.model_validate(
                        document.extraction_json, strict=True
                    ),
                    freeze_reason=None,
                )
            report = ForensicsReport.model_validate(document.forensics_json)
            if report.detector_enabled != detector_enabled:
                raise ParseUnavailable("evidence detector mode mismatch")
            return extraction, report
        if replayed:
            raise ParseUnavailable("recorded evidence unavailable")

        pdf = await asyncio.to_thread(_read_pdf, document.storage_path, document.sha256)
        parsed = await parse_pdf(pdf, detector_enabled=detector_enabled)

        def run(complete):
            extraction = extract_invoice(
                complete,
                text=parsed.text,
                page_images=[base64.b64decode(image, validate=True) for image in parsed.images],
            )
            return extraction, classify(
                complete,
                text=parsed.text,
                segments=parsed.segments,
                detector_enabled=detector_enabled,
                detector_version=DETECTOR_VERSION,
            )

        extraction, report = await asyncio.to_thread(
            governed_analysis, _budget_id(execution_id), settings=settings, action=run
        )
        async with sessions.begin() as session:
            row = await session.get(InvoiceDocument, document.document_id)
            row.extraction_json = (
                extraction.extraction.model_dump(mode="json") if extraction.extraction else None
            )
            row.forensics_json = report.model_dump(mode="json")
        return extraction, report

    return analyze
