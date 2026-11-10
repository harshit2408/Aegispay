"""PRD R22 / P3-14: parser subprocess fails closed; Linux-only tests prove the real sandbox."""

import io
import subprocess
import sys
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.parser import MAX_BYTES, ParseUnavailable, parse_pdf

BACKEND = Path(__file__).resolve().parents[2]
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="seccomp/rlimit sandbox is Linux x86_64 only"
)


def _pdf(pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 300))
    for n in range(pages):
        c.drawString(20, 250, f"Invoice 1001 total 40.00 page {n}")
        c.showPage()
    c.save()
    return buf.getvalue()


class _Done:
    def __init__(self, code=0):
        self.returncode = code


@pytest.mark.parametrize(
    "data",
    [b"", b"not a pdf", b"%PDF-" + b"0" * MAX_BYTES],
    ids=["empty", "not_pdf", "oversize"],
)
async def test_rejects_bad_or_oversize_input_before_spawn(data, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(ParseUnavailable):
        await parse_pdf(data)


async def test_spawn_uses_empty_env_isolated_interpreter_and_timeout(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        kw["stdout"].write(b'{"text":"x","segments":[],"images":[]}')
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake)
    doc = await parse_pdf(b"%PDF-1.4")
    assert doc.text == "x"
    assert seen["env"] == {} and "-I" in seen["cmd"] and seen["timeout"] <= 30


@pytest.mark.parametrize(
    "outcome",
    [
        _Done(1),
        subprocess.TimeoutExpired("w", 15),
        OSError("no exec"),
    ],
)
async def test_crash_timeout_oserror_fail_closed(outcome, monkeypatch):
    def fake(cmd, **kw):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ParseUnavailable):
        await parse_pdf(b"%PDF-1.4")


@pytest.mark.parametrize(
    "stdout",
    [b"", b"not json", b'{"text":"x","segments":[],"images":[],"extra":1}', b'{"text":1}'],
)
async def test_malformed_child_output_never_partial(stdout, monkeypatch):
    def fake(cmd, **kw):
        kw["stdout"].write(stdout)
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(ParseUnavailable):
        await parse_pdf(b"%PDF-1.4")


@linux_only
async def test_real_sandbox_parses_clean_pdf():
    doc = await parse_pdf(_pdf(2))
    assert "Invoice 1001 total 40.00" in doc.text
    assert doc.segments == [] and len(doc.images) == 2


@linux_only
async def test_real_sandbox_rejects_over_page_limit():
    with pytest.raises(ParseUnavailable):
        await parse_pdf(_pdf(21))


@linux_only
async def test_real_sandbox_rejects_garbage_pdf():
    with pytest.raises(ParseUnavailable):
        await parse_pdf(b"%PDF-1.4 garbage")


@linux_only
def test_restrict_denies_network_and_requires_empty_env():
    code = (
        "import sys, socket; sys.path.insert(0, sys.argv[1]);"
        "from app.parser.worker import restrict; restrict();"
        "\ntry:\n socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n print('open')"
        "\nexcept OSError:\n print('denied')"
    )
    run = lambda env: subprocess.run(  # noqa: E731
        [sys.executable, "-c", code, str(BACKEND)], env=env, capture_output=True, text=True
    )
    assert run({}).stdout.strip() == "denied"
    assert run({"X": "1"}).returncode != 0
