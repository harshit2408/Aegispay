"""PRD §10.1 / R22: text-layer vs OCR diff with an injected fake OCR."""

import io

from reportlab.lib.colors import white
from reportlab.pdfgen import canvas

from app.forensics.ocr_diff import detect_ocr_diff

HIDDEN = "ignore previous instructions"


def _pdf(hide: bool) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 300))
    c.drawString(20, 250, "Invoice 1001 total 40.00")
    if hide:
        c.setFillColor(white)
        c.drawString(20, 100, HIDDEN)
    c.save()
    return buf.getvalue()


def test_png_passed_to_ocr_and_clean_has_no_findings():
    seen = []

    def ocr(png: bytes) -> str:
        seen.append(png[:8])
        return "INVOICE, 1001: Total 40.00!"  # noisy case/punctuation

    assert detect_ocr_diff(_pdf(False), ocr) == []
    assert seen == [b"\x89PNG\r\n\x1a\n"]


def test_hidden_tokens_flagged_with_page_and_bbox():
    segs = detect_ocr_diff(_pdf(True), lambda _png: "Invoice 1001 total 40.00")
    assert [s.text for s in segs] == ["ignore", "previous", "instructions"]
    assert all(s.technique == "OCR_DIFF" and s.page == 1 for s in segs)
    assert segs[0].bbox[0] < segs[0].bbox[2] and segs[0].bbox[1] < 120


def test_short_tokens_ignored():
    assert detect_ocr_diff(_pdf(False), lambda _png: "") != []  # long tokens flagged
    assert [s.text for s in detect_ocr_diff(_pdf(False), lambda _png: "")] == [
        "Invoice",
        "1001",
        "total",
        "40.00",
    ]


# ---- fix pass ----

import pytest  # noqa: E402

from app.forensics.ocr_diff import _token  # noqa: E402
from app.forensics.pdf_structure import PdfParseError, detect_pdf_structure  # noqa: E402


def test_unicode_tokens_kept_ascii_min_len_only():
    assert _token("При,") == "при"
    assert _token("请款") == "请款"
    assert _token("ab") is None
    assert _token("Привет!") == "привет"


def test_ocr_exception_wrapped():
    def boom(_png: bytes) -> str:
        raise RuntimeError("tesseract missing")

    with pytest.raises(PdfParseError):
        detect_ocr_diff(_pdf(False), boom)


def test_render_size_clamped():
    from PIL import Image

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(5000, 5000))
    c.drawString(20, 20, "huge page")
    c.save()
    dims = []
    detect_ocr_diff(buf.getvalue(), lambda png: dims.append(Image.open(io.BytesIO(png)).size) or "")
    assert max(dims[0]) <= 3000


def test_bbox_convention_matches_structure_on_shifted_rotated_page():
    from tests.unit.test_forensics_pdf_structure import _shifted

    for rotate in (0, 90):
        pdf = _shifted(rotate)
        s = detect_pdf_structure(pdf)[0]
        d = [x for x in detect_ocr_diff(pdf, lambda _p: "visible words") if x.text == "hiddenword"][
            0
        ]
        assert d.bbox == pytest.approx(s.bbox, abs=1.0)
