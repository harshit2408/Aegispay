"""R22 PDF boundary: the API never imports a PDF parser or native rendering library."""

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.decision import HiddenSegment

MAX_BYTES = 10 * 1024 * 1024
MAX_RESULT = 16 * 1024 * 1024


class ParseUnavailable(ValueError):
    pass


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=200000)
    segments: list[HiddenSegment] = Field(max_length=1000)
    images: list[str] = Field(max_length=20)


def _parse(pdf: bytes, detector: bool) -> ParsedDocument:
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_BYTES:
        raise ParseUnavailable("PDF must be at most 10 MB")
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-parser-") as directory:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(
                    [sys.executable, "-I", str(Path(__file__).with_name("worker.py")),
                     "on" if detector else "off"],
                    input=pdf, stdout=output, stderr=subprocess.DEVNULL,
                    env={}, cwd=directory, timeout=15, check=False,
                )
                if result.returncode:
                    raise ParseUnavailable("PDF parsing failed closed")
                output.seek(0)
                raw = output.read(MAX_RESULT + 1)
                if len(raw) > MAX_RESULT:
                    raise ParseUnavailable("PDF parser output limit exceeded")
                return ParsedDocument.model_validate(json.loads(raw))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ParseUnavailable("PDF parsing unavailable or invalid") from None


async def parse_pdf(pdf: bytes, *, detector_enabled: bool = True) -> ParsedDocument:
    return await asyncio.to_thread(_parse, pdf, detector_enabled)
