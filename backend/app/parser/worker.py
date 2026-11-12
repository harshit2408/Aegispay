"""Untrusted PDF subprocess: empty environment, resource limits and kernel network denial."""

import base64
import ctypes
import errno
import io
import json
import os
import platform
import resource
import sys


def restrict() -> None:
    # Parent passes env={}; CPython's C-locale coercion (PEP 538) adds LC_CTYPE itself.
    if sys.platform != "linux" or platform.machine() != "x86_64" or set(os.environ) - {"LC_CTYPE"}:
        raise RuntimeError("parser isolation unavailable")
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)  # PRD §16
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    # Check the syscall ABI before numbers; reject x32, networking/io_uring syscalls and
    # setpgid (109) / setsid (112) so nothing can leave the group the parent SIGKILLs.
    rules = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x80000000),
    ]
    for syscall in (*range(41, 56), 109, 112, 288, 299, 307, 425):
        rules.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, 0x00050000 | errno.EPERM)])
    rules.append((0x06, 0, 0, 0x7FFF0000))
    filters = (Filter * len(rules))(*(Filter(*row) for row in rules))
    program = Program(len(rules), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise RuntimeError("parser network isolation unavailable")
    # Worker-local, inherited by tesseract; the parent still passes env={}.
    os.environ["OMP_THREAD_LIMIT"] = "1"


def main() -> None:
    restrict()
    # Native/PDF libraries are loaded only after isolation is established.
    import pdfplumber
    import pypdfium2 as pdfium
    import pytesseract

    from app.forensics.ocr_diff import detect_ocr_diff, tesseract_ocr
    from app.forensics.pdf_structure import detect_pdf_structure

    pdf = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if not pdf.startswith(b"%PDF-") or len(pdf) > 10 * 1024 * 1024:
        raise ValueError("invalid PDF")
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off"):
        raise ValueError("invalid parser mode")
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        if not 0 < len(document.pages) <= 20:
            raise ValueError("PDF page limit")
        text = "\n".join((page.extract_text() or "")[:10000] for page in document.pages)
    segments = []
    if sys.argv[1] == "on":
        segments = detect_pdf_structure(pdf) + detect_ocr_diff(pdf, tesseract_ocr)
    if len(segments) > 1000:
        raise ValueError("PDF finding limit")
    images, image_bytes = [], 0
    document = pdfium.PdfDocument(pdf)
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                scale = min(1.5, 1200 / max(*page.get_size(), 1))
                bitmap = page.render(scale=scale)
                try:
                    image = io.BytesIO()
                    bitmap.to_pil().save(image, format="PNG")
                    image_bytes += len(image.getvalue())
                    if image_bytes > 8 * 1024 * 1024:
                        raise ValueError("rendered image limit")
                    images.append(base64.b64encode(image.getvalue()).decode("ascii"))
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        document.close()
    result = {"text": text, "segments": [s.model_dump() for s in segments], "images": images}
    sys.stdout.write(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
