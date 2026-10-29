"""PRD §10.1 / R22: deterministic structural hidden-text detection. No LLM, no tools.

Coordinate convention (shared with ocr_diff): pdfminer page space, y up, origin at the
MediaBox lower-left after page rotation. Unrotated zero-origin pages: plain PDF space.
"""

import io
import math
from collections.abc import Iterator

import pdfplumber
from pdfminer.layout import LTChar, LTComponent, LTContainer, LTRect
from pypdf import PdfReader
from pypdf.generic import ContentStream, DictionaryObject

from app.schemas.decision import HiddenSegment

TINY_FONT_PT = 2.0
COLOR_DELTA_E = 3.0
LONG_INFO_VALUE = 60
MAX_INFO_WORDS = 8
WORDY_INFO_KEYS = {"/Title", "/Subject", "/Keywords"}
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_CHARS_PER_PAGE = 10_000
MAX_RECTS_PER_PAGE = 500  # bounds chars x rects work: 10k x 500 = 5M box checks
MAX_FORM_DEPTH = 5
MAX_FORM_CALLS = 200
# Subprocess isolation and wall-clock timeout belong to P3-14.
STANDARD_INFO_KEYS = {
    "/Title",
    "/Author",
    "/Subject",
    "/Keywords",
    "/Creator",
    "/Producer",
    "/CreationDate",
    "/ModDate",
    "/Trapped",
}
# Highest priority first: a char gets one technique.
_PRIORITY = ("OFF_PAGE", "INVISIBLE_MODE", "TINY_FONT", "COLOR_MATCH", "COVERED")


class PdfParseError(Exception):
    """Unreadable or encrypted PDF. Callers fail closed."""


def _to_rgb(color) -> tuple[float, float, float] | None:
    """Gray/RGB/CMYK -> RGB 0..1; None for patterns or unknown."""
    if color is None:
        return (0.0, 0.0, 0.0)
    if isinstance(color, (int, float)):
        color = (color,)
    try:
        v = [float(x) for x in color]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        return (v[0], v[0], v[0])
    if len(v) == 3:
        return (v[0], v[1], v[2])
    if len(v) == 4:
        c, m, y, k = v
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    def lin(u: float) -> float:
        u = min(max(u, 0.0), 1.0)
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(u) for u in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _delta_e(a, b) -> float:
    return math.dist(_lab(a), _lab(b))


def _flatten(container) -> Iterator[LTComponent]:
    """Yield leaf objects in content-stream paint order (forms are inlined where drawn)."""
    for o in container:
        if isinstance(o, LTContainer):
            yield from _flatten(o)
        else:
            yield o


def _center_in(box, outer) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def layout_crop_box(page) -> tuple[float, float, float, float]:
    """CropBox mapped into pdfminer page space (same CTM pdfminer applies to glyphs)."""
    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    rot = (getattr(page, "rotation", 0) or 0) % 360
    a, b, c, d, e, f = {
        0: (1, 0, 0, 1, -x0, -y0),
        90: (0, -1, 1, 0, -y0, x1),
        180: (-1, 0, 0, -1, x1, y1),
        270: (0, 1, -1, 0, y1, -x0),
    }[rot]
    cx0, cy0, cx1, cy1 = (float(v) for v in page.cropbox)
    pts = [(a * x + c * y + e, b * x + d * y + f) for x in (cx0, cx1) for y in (cy0, cy1)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def page_objects(page) -> list[LTComponent]:
    """Leaf layout objects of one page; raises PdfParseError past the R22 char/rect caps."""
    objs = list(_flatten(page.layout))
    n_chars = sum(isinstance(o, LTChar) for o in objs)
    n_rects = sum(isinstance(o, LTRect) for o in objs)
    if n_chars > MAX_CHARS_PER_PAGE or n_rects > MAX_RECTS_PER_PAGE:
        raise PdfParseError("page exceeds character/shape limits")
    return objs


def _collect_invisible(data, resources, reader, out, mode, depth, budget) -> None:
    """Strings shown in render mode 3/7; tracks q/Q and recurses into form XObjects."""
    stack: list[int] = []
    for operands, op in ContentStream(data, reader).operations:
        if op == b"q":
            stack.append(mode)
        elif op == b"Q" and stack:
            mode = stack.pop()
        elif op == b"Tr" and operands:
            mode = int(operands[0])
        elif mode in (3, 7) and op in (b"Tj", b"'", b'"', b"TJ"):
            items = operands[0] if op == b"TJ" else operands[-1:]
            out.extend(_as_str(i) for i in items if isinstance(i, (str, bytes)))
        elif op == b"Do" and operands:
            budget[0] -= 1
            if budget[0] < 0 or depth >= MAX_FORM_DEPTH:
                raise PdfParseError("form XObject nesting/call limit exceeded")
            xobjs = resources.get("/XObject") if resources else None
            xo = xobjs.get_object().get(operands[0]) if xobjs else None
            xo = xo.get_object() if xo is not None else None
            if xo is not None and xo.get("/Subtype") == "/Form":
                res = xo.get("/Resources")
                res = res.get_object() if res is not None else resources
                _collect_invisible(xo, res, reader, out, mode, depth + 1, budget)


def _invisible_texts(reader: PdfReader, page_index: int) -> list[str]:
    page = reader.pages[page_index]
    contents = page.get_contents()
    if contents is None:
        return []
    res = page.get("/Resources")
    out: list[str] = []
    _collect_invisible(
        contents, res.get_object() if res is not None else None, reader, out, 0, 0, [MAX_FORM_CALLS]
    )
    return [t for t in out if t.strip()]


def _as_str(v) -> str:
    return v if isinstance(v, str) else v.decode("latin-1")


def _mark_invisible(chars: list[LTChar], texts: list[str]) -> tuple[set[int], list[str]]:
    """Char indexes matching mode-3 strings, plus strings that match no glyph (fail closed)."""
    joined = "".join(c.get_text() for c in chars)
    marked: set[int] = set()
    unmatched: list[str] = []
    for t in texts:
        start = joined.find(t)
        if start == -1:
            unmatched.append(t)
        while start != -1:
            marked.update(range(start, start + len(t)))
            start = joined.find(t, start + len(t))
    return marked, unmatched


def _page_segments(plumber_page, reader: PdfReader, idx: int) -> list[HiddenSegment]:
    objs = page_objects(plumber_page)
    crop = layout_crop_box(plumber_page)
    chars = [o for o in objs if isinstance(o, LTChar)]
    invisible, unmatched = _mark_invisible(chars, _invisible_texts(reader, idx))
    rects = [(i, o, _to_rgb(o.non_stroking_color)) for i, o in enumerate(objs) if _is_fill(o)]
    flags: list[str | None] = []
    ci = 0
    for i, o in enumerate(objs):
        if not isinstance(o, LTChar):
            continue
        found: set[str] = set()
        if not _center_in(o.bbox, crop):
            found.add("OFF_PAGE")
        if ci in invisible:
            found.add("INVISIBLE_MODE")
        if o.size < TINY_FONT_PT:
            found.add("TINY_FONT")
        fg = _to_rgb(o.graphicstate.ncolor)
        under = [r for r in rects if r[0] < i and _center_in(o.bbox, r[1].bbox)]
        bg = under[-1][2] if under else (1.0, 1.0, 1.0)
        if fg is None:  # Pattern/unknown colour space: cannot prove visible
            found.add("COLOR_MATCH")
        elif bg is not None and _delta_e(fg, bg) < COLOR_DELTA_E:
            found.add("COLOR_MATCH")
        if any(r[0] > i and _center_in(o.bbox, r[1].bbox) for r in rects):
            found.add("COVERED")
        flags.append(next((t for t in _PRIORITY if t in found), None))
        ci += 1
    segs = _merge(chars, flags, idx + 1)
    segs += [
        HiddenSegment(page=idx + 1, bbox=crop, technique="INVISIBLE_MODE", text=t)
        for t in unmatched
    ]
    return segs


def _is_fill(o) -> bool:
    return isinstance(o, LTRect) and bool(o.fill)


def _merge(chars: list[LTChar], flags: list[str | None], page: int) -> list[HiddenSegment]:
    segs: list[HiddenSegment] = []
    run: list[LTChar] = []
    tech: str | None = None

    def flush() -> None:
        text = "".join(c.get_text() for c in run).strip()
        if tech and text:
            box = (
                min(c.x0 for c in run),
                min(c.y0 for c in run),
                max(c.x1 for c in run),
                max(c.y1 for c in run),
            )
            segs.append(HiddenSegment(page=page, bbox=box, technique=tech, text=text))

    for c, t in zip(chars, flags, strict=True):
        if t != tech:
            flush()
            run, tech = [], t
        if t:
            run.append(c)
    flush()
    return segs


def _meta(page: int, text: str) -> HiddenSegment:
    return HiddenSegment(page=page, bbox=(0.0, 0.0, 0.0, 0.0), technique="METADATA", text=text)


_ANNOT_TEXT_KEYS = ("/Contents", "/V", "/TU", "/RC", "/Subj")


def _text_of(v) -> str:
    v = v.get_object() if hasattr(v, "get_object") else v
    if isinstance(v, str):
        return v.strip()
    if hasattr(v, "get_data"):  # rich-text stream
        return v.get_data().decode("utf-8", "ignore").strip()
    return ""


def _annotations(reader: PdfReader) -> list[HiddenSegment]:
    out = []
    for n, page in enumerate(reader.pages, start=1):
        annots = page.get("/Annots")
        for ref in annots.get_object() if annots is not None else []:
            a = ref.get_object()
            parts = [_text_of(a[k]) for k in _ANNOT_TEXT_KEYS if k in a]
            text = " ".join(p for p in parts if p)
            if text:
                rect = a["/Rect"].get_object() if "/Rect" in a else (0, 0, 0, 0)
                box = tuple(float(v.get_object() if hasattr(v, "get_object") else v) for v in rect)
                out.append(HiddenSegment(page=n, bbox=box, technique="ANNOTATION", text=text))
    return out


def _info_suspicious(key: str, value: str) -> bool:
    if key not in STANDARD_INFO_KEYS or len(value) > LONG_INFO_VALUE:
        return True
    return key in WORDY_INFO_KEYS and len(value.split()) > MAX_INFO_WORDS


def _metadata(reader: PdfReader) -> list[HiddenSegment]:
    out = [
        _meta(1, str(v)) for k, v in (reader.metadata or {}).items() if _info_suspicious(k, str(v))
    ]
    root = reader.trailer["/Root"].get_object()
    names = root["/Names"].get_object() if "/Names" in root else DictionaryObject()
    if "/EmbeddedFiles" in names:
        out.append(_meta(1, "embedded files"))
    if "/JavaScript" in names or "/OpenAction" in root or "/AA" in root:
        out.append(_meta(1, "javascript or open action"))
    return out


def detect_pdf_structure(pdf: bytes) -> list[HiddenSegment]:
    """Raise PdfParseError on malformed/encrypted/oversized input; callers fail closed."""
    check_size(pdf)
    try:
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted:
            raise PdfParseError("encrypted PDF")
        if len(reader.pages) > MAX_PAGES:
            raise PdfParseError("too many pages")
        segs: list[HiddenSegment] = []
        with pdfplumber.open(io.BytesIO(pdf), laparams=None) as doc:
            for idx, page in enumerate(doc.pages):
                segs.extend(_page_segments(page, reader, idx))
        return segs + _annotations(reader) + _metadata(reader)
    except PdfParseError:
        raise
    except Exception as exc:  # any parser failure is untrusted input -> fail closed
        raise PdfParseError(f"unreadable PDF: {exc}") from exc


def check_size(pdf: bytes) -> None:
    if len(pdf) > MAX_PDF_BYTES:
        raise PdfParseError("PDF exceeds size limit")
