"""PRD R22 / P3-14: parser subprocess fails closed; Linux-only tests prove the real sandbox."""

import io
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.parser import MAX_BYTES, ParseUnavailable, parse_pdf

BACKEND = Path(__file__).resolve().parents[2]
linux_only = pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64",
    reason="seccomp/rlimit sandbox is Linux x86_64 only",
)


def _pdf(pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 300))
    for n in range(pages):
        c.drawString(20, 250, f"Invoice 1001 total 40.00 page {n}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _fake_child(monkeypatch, *, stdout=b"", code=0, error=None):
    """Replace the parser process; returns what it saw and the process groups killed."""
    seen, killed = {}, []

    class Child:
        pid, returncode = 4242, code

        def __init__(self, cmd, **kw):
            seen.update(cmd=cmd, **kw)
            if isinstance(error, OSError):
                raise error
            kw["stdout"].write(stdout)

        def communicate(self, data, timeout):
            seen["timeout"] = timeout
            if isinstance(error, subprocess.TimeoutExpired):
                raise error

        def wait(self):
            return code

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(subprocess, "Popen", Child)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append(pid), raising=False)
    return seen, killed


@pytest.mark.parametrize(
    "data",
    [b"", b"not a pdf", b"%PDF-" + b"0" * MAX_BYTES],
    ids=["empty", "not_pdf", "oversize"],
)
async def test_rejects_bad_or_oversize_input_before_spawn(data, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(ParseUnavailable):
        await parse_pdf(data)


async def test_spawn_uses_empty_env_isolated_interpreter_own_group_and_timeout(monkeypatch):
    seen, killed = _fake_child(monkeypatch, stdout=b'{"text":"x","segments":[],"images":[]}')
    doc = await parse_pdf(b"%PDF-1.4")
    assert doc.text == "x"
    assert seen["env"] == {} and "-I" in seen["cmd"] and seen["timeout"] <= 30
    assert seen["start_new_session"] is True and killed == [4242]


@pytest.mark.parametrize(
    "outcome",
    [
        {"code": 1},
        {"error": subprocess.TimeoutExpired("w", 15)},
        {"error": OSError("no exec")},
    ],
    ids=["crash", "timeout", "no_exec"],
)
async def test_crash_timeout_oserror_fail_closed(outcome, monkeypatch):
    _, killed = _fake_child(monkeypatch, **outcome)
    with pytest.raises(ParseUnavailable):
        await parse_pdf(b"%PDF-1.4")
    assert killed == (
        [] if "error" in outcome and isinstance(outcome["error"], OSError) else [4242]
    )


@pytest.mark.parametrize(
    "stdout",
    [b"", b"not json", b'{"text":"x","segments":[],"images":[],"extra":1}', b'{"text":1}'],
)
async def test_malformed_child_output_never_partial(stdout, monkeypatch):
    _fake_child(monkeypatch, stdout=stdout)
    with pytest.raises(ParseUnavailable):
        await parse_pdf(b"%PDF-1.4")


@linux_only
async def test_real_sandbox_parses_clean_pdf():
    doc = await parse_pdf(_pdf(2), detector_enabled=False)  # tesseract not required in CI
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
        [sys.executable, "-I", "-c", code, str(BACKEND)], env=env, capture_output=True, text=True
    )
    assert run({}).stdout.strip() == "denied"
    assert run({"X": "1"}).returncode != 0


@linux_only
def test_restrict_applies_prd_resource_limits():
    code = (
        "import json, resource, sys; sys.path.insert(0, sys.argv[1]);"
        "from app.parser.worker import restrict; restrict();"
        "print(json.dumps([resource.getrlimit(getattr(resource, 'RLIMIT_' + n))[1]"
        " for n in ('CPU', 'AS', 'FSIZE', 'NOFILE', 'CORE')]))"
    )
    run = subprocess.run(
        [sys.executable, "-I", "-c", code, str(BACKEND)], env={}, capture_output=True, text=True
    )
    cpu, memory, fsize, nofile, core = json.loads(run.stdout)
    assert 0 < cpu <= 30 and memory == 512 * 1024 * 1024  # PRD §16: 30 s / 512 MB
    assert fsize <= 16 * 1024 * 1024 and nofile <= 64 and core == 0


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"  # an unreaped zombie holds no CPU, memory or files


@pytest.mark.skipif(sys.platform != "linux", reason="process groups and /proc are Linux-only")
async def test_timeout_kills_the_parser_and_everything_it_spawned(tmp_path, monkeypatch):
    pids = tmp_path / "pids"
    fake = tmp_path / "hung-parser"
    fake.write_text(f"#!/bin/sh\n/bin/sleep 60 &\necho $$ $! > {pids}\nwait\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake))
    monkeypatch.setattr("app.parser.TIMEOUT_SECONDS", 1)
    with pytest.raises(ParseUnavailable):
        await parse_pdf(b"%PDF-1.4")
    parser, grandchild = map(int, pids.read_text().split())
    assert not _alive(parser) and not _alive(grandchild)
