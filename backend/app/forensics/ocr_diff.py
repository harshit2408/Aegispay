"""PRD §10.1 / R22: flag text-layer tokens that OCR of the rendered page cannot see.

Bboxes use the same page space as pdf_structure (y up, MediaBox origin, after rotation).
"""

import io
import math
import re
from collections.abc import Callable

import pdfplumber
import pypdfium2 as pdfium
from pdfminer.layout import LTChar

from app.forensics.pdf_structure import (
    MAX_PAGES,
    PdfParseError,
    check_size,
    page_objects,
)
from app.schemas.decision import HiddenSegment

MIN_ASCII_TOKEN_LEN = 3
MAX_RENDER_PX = 3000  # longest rendered side; bounds memory for huge MediaBoxes
# Subprocess isolation and wall-clock timeout belong to P3-14.


def _token(text: str) -> str | None:
    """Casefolded word characters only. Short ASCII tokens are noise; CJK/Cyrillic are kept."""
    n = re.sub(r"\W", "", text.casefold())
    if not n or (n.isascii() and len(n) < MIN_ASCII_TOKEN_LEN):
        return None
    return n


def tesseract_ocr(png: bytes) -> str:
    """Optional adapter; pytesseract and the tesseract binary are needed only when called."""
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(io.BytesIO(png)))


def _words(chars: list[LTChar]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Whitespace-delimited words from layout chars (same coordinates as pdf_structure)."""
    words, run = [], []
    for c in [*chars, None]:
        if c is not None and not c.get_text().isspace():
            if run and _gap(run[-1], c) > 0.5 * c.size:  # new line/column without a space glyph
                words.append(_word(run))
                run = []
            run.append(c)
            continue
        if run:
            words.append(_word(run))
            run = []
    return words


def _gap(a: LTChar, b: LTChar) -> float:
    return math.hypot(max(0.0, b.x0 - a.x1, a.x0 - b.x1), max(0.0, b.y0 - a.y1, a.y0 - b.y1))


def _word(run: list[LTChar]) -> tuple[str, tuple[float, float, float, float]]:
    box = (
        min(r.x0 for r in run),
        min(r.y0 for r in run),
        max(r.x1 for r in run),
        max(r.y1 for r in run),
    )
    return "".join(r.get_text() for r in run), box


def _render_png(page: "pdfium.PdfPage", dpi: int) -> bytes:
    w, h = page.get_size()
    scale = min(dpi / 72, MAX_RENDER_PX / max(w, h, 1.0))
    buf = io.BytesIO()
    page.render(scale=scale).to_pil().save(buf, format="PNG")
    return buf.getvalue()


def detect_ocr_diff(pdf: bytes, ocr: Callable[[bytes], str], dpi: int = 200) -> list[HiddenSegment]:
    check_size(pdf)
    try:
        doc = pdfium.PdfDocument(pdf)
    except Exception as exc:
        raise PdfParseError(f"unreadable PDF: {exc}") from exc
    try:
        if len(doc) > MAX_PAGES:
            raise PdfParseError("too many pages")
        with pdfplumber.open(io.BytesIO(pdf), laparams=None) as plumber:
            segs: list[HiddenSegment] = []
            for idx, page in enumerate(plumber.pages):
                png = _render_png(doc[idx], dpi)
                try:
                    text = ocr(png)
                except Exception as exc:
                    raise PdfParseError(f"OCR failed: {exc}") from exc
                seen = {t for t in map(_token, text.split()) if t}
                chars = [o for o in page_objects(page) if isinstance(o, LTChar)]
                for word, box in _words(chars):
                    n = _token(word)
                    if n and n not in seen:
                        segs.append(
                            HiddenSegment(page=idx + 1, bbox=box, technique="OCR_DIFF", text=word)
                        )
            return segs
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError(f"unreadable PDF: {exc}") from exc
    finally:
        doc.close()
