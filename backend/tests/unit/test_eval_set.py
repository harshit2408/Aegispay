import importlib.util
import json
from collections import Counter
from pathlib import Path

import pypdf
import pytest

from app.eval import runner
from app.schemas.decision import HiddenSegment

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "tests" / "fixtures" / "eval"
FIELDS = {
    "vendor_invoice_number",
    "po_reference",
    "payee_email",
    "billed_qty",
    "unit_price_cents",
    "total_cents",
}


def _manifest():
    return json.loads((EVAL / "manifest.json").read_text(encoding="utf-8"))


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_eval_set", ROOT / "scripts" / "build_eval_set.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifest_invariants():
    m = _manifest()
    assert len(m) >= 50
    per_class = Counter(e["class"] for e in m)
    assert min(per_class.values()) >= 4
    assert len({e["id"] for e in m}) == len(m)
    held = sum(e["split"] == "heldout" for e in m) / len(m)
    assert 0.2 <= held <= 0.4
    for cls in per_class:
        assert {e["split"] for e in m if e["class"] == cls} == {"dev", "heldout"}
    for e in m:
        assert set(e["expected_fields"]) == FIELDS
        assert e["expected_label"] in {"CLEAN", "SUSPICIOUS", "INJECTION"}
        assert (EVAL / e["file"]).stat().st_size < 20_000
        assert len(pypdf.PdfReader(EVAL / e["file"]).pages) == 1
    assert all(e["paypal_note"] for e in m if e["class"] == "vendor_memo")


def test_regeneration_is_deterministic(tmp_path):
    b = _builder()
    b.build(tmp_path)
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == _manifest()


def test_runner_scores_with_stub_detector():
    def stub(pdf: bytes) -> list[HiddenSegment]:
        return (
            [HiddenSegment(page=0, bbox=(0, 0, 1, 1), technique="COLOR_MATCH", text="x")]
            if b"%PDF" in pdf
            else []
        )

    res = runner.run_detector(stub, "dev")
    assert res["white_on_white"]["metric"] == "recall"
    assert res["white_on_white"]["successes"] == res["white_on_white"]["trials"]
    assert res["clean"]["metric"] == "false_positive"
    assert res["clean"]["successes"] == 0  # stub flags everything: no case is correctly clean
    assert res["clean"]["n_of_m"].startswith("0 of ")

    none = runner.run_detector(lambda pdf: [], "dev")
    assert none["clean"]["successes"] == none["clean"]["trials"]
    assert none["white_on_white"]["successes"] == 0


def test_runner_rejects_mixed_class(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    cases = [
        {"id": i, "split": "dev", "class": "c", "file": "a.pdf", "expected_techniques": t}
        for i, t in (("1", []), ("2", ["TINY_FONT"]))
    ]
    (tmp_path / "manifest.json").write_text(json.dumps(cases), encoding="utf-8")
    with pytest.raises(AssertionError):
        runner.run_detector(lambda pdf: [], "dev", tmp_path)


def test_runner_llm_stubs_repeat_five_times():
    calls = []

    def classify(pdf: bytes, note: str | None) -> str:
        calls.append(1)
        return "CLEAN"

    res = runner.run_classifier(classify, "heldout")
    assert res["clean"]["runs"] == 5
    assert len(calls) % 5 == 0
    ext = runner.run_extraction(lambda pdf: {}, "heldout")
    assert ext["classes"]["clean"]["successes"] == 0
    assert ext["fields"]["total_cents"].startswith("0 of ")
