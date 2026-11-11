"""Static guards on the S1 stack files (repo-root files are skipped in a backend-only copy)."""

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
needs_root = pytest.mark.skipif(
    not (ROOT / "docker-compose.s1.yml").exists(), reason="repo root files not present"
)


def test_dockerfile_installs_the_ocr_binary_the_parser_hardcodes_by_default():
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    worker = (BACKEND / "app" / "parser" / "worker.py").read_text(encoding="utf-8")
    assert '"/usr/bin/tesseract"' in worker  # Debian's tesseract-ocr installs exactly there
    assert "ARG INSTALL_TESSERACT=1" in dockerfile and "tesseract-ocr" in dockerfile


@needs_root
def test_settlement_image_stays_without_the_pdf_toolchain():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert re.search(r'INSTALL_GIT: "1"\s+INSTALL_TESSERACT: "0"', compose)
    assert re.search(r'key: INSTALL_TESSERACT.*\n\s+value: "0"', render)


@needs_root
def test_s1_overlay_holds_no_literal_secret():
    overlay = (ROOT / "docker-compose.s1.yml").read_text(encoding="utf-8")
    for line in overlay.splitlines():
        if re.search(r"(PASSWORD|SECRET|TOKEN|KEY)\b", line) and not line.lstrip().startswith("#"):
            assert "${" in line, line  # every secret is substituted from an ignored env file


@needs_root
def test_generated_secret_files_are_git_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {".env.*", "operator-seed*.json", "s1-out/"} <= set(ignore)
