"""Eval runner. Detectors and LLM components are injected; no live calls here."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.eval.stats import per_class_summary
from app.schemas.decision import HiddenSegment

EVAL_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "eval"
REPEATS = 5

Detector = Callable[[bytes], list[HiddenSegment]]
Extractor = Callable[[bytes], dict[str, Any]]
Classifier = Callable[[bytes, str | None], str]


def load_cases(split: str, eval_dir: Path = EVAL_DIR) -> list[dict]:
    if split not in ("dev", "heldout"):
        raise ValueError("split must be dev or heldout")
    cases = json.loads((eval_dir / "manifest.json").read_text(encoding="utf-8"))
    return [c for c in cases if c["split"] == split]


def _pdf(case: dict, eval_dir: Path) -> bytes:
    return (eval_dir / case["file"]).read_bytes()


def run_detector(detect: Detector, split: str, eval_dir: Path = EVAL_DIR) -> dict[str, dict]:
    """Success counts are always the good outcome, so n_of_m reads "n good of m".
    Recall classes (expect techniques): success = all expected techniques found.
    False-positive classes (expect none): success = case correctly NOT flagged, so
    n_of_m is "n clean cases not flagged of m"; FP rate = 1 - n/m. Each class must be
    homogeneous. Deterministic: one run."""
    res: dict[str, list[bool]] = defaultdict(list)
    kind: dict[str, str] = {}
    for case in load_cases(split, eval_dir):
        found = {s.technique for s in detect(_pdf(case, eval_dir))}
        want = set(case["expected_techniques"])
        cls = case["class"]
        k = "recall" if want else "false_positive"
        assert kind.setdefault(cls, k) == k, f"class {cls} mixes expecting and non-expecting cases"
        res[cls].append(want <= found if want else not found)
    summary = per_class_summary({c: [v] for c, v in res.items()})
    for c, s in summary.items():
        s["metric"] = kind[c]
    return summary


def _repeat(
    fn: Callable[[dict], bool], cases: list[dict], repeats: int
) -> dict[str, list[list[bool]]]:
    out: dict[str, list[list[bool]]] = {}
    for cls in {c["class"] for c in cases}:
        mine = [c for c in cases if c["class"] == cls]
        out[cls] = [[fn(c) for c in mine] for _ in range(repeats)]
    return out


def run_classifier(
    classify: Classifier, split: str, repeats: int = REPEATS, eval_dir: Path = EVAL_DIR
) -> dict[str, dict]:
    cases = [c for c in load_cases(split, eval_dir) if c["llm_required"]]
    ok = lambda c: classify(_pdf(c, eval_dir), c["paypal_note"]) == c["expected_label"]  # noqa: E731
    return per_class_summary(_repeat(ok, cases, repeats))


def run_extraction(
    extract: Extractor, split: str, repeats: int = REPEATS, eval_dir: Path = EVAL_DIR
) -> dict:
    """Case-level exact match per class, plus field-level accuracy over all trials."""
    cases = [c for c in load_cases(split, eval_dir) if c["llm_required"]]
    field_hits: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def ok(c: dict) -> bool:
        got = extract(_pdf(c, eval_dir))
        exact = True
        for name, want in c["expected_fields"].items():
            hit = got.get(name) == want
            field_hits[name][0] += hit
            field_hits[name][1] += 1
            exact &= hit
        return exact

    classes = per_class_summary(_repeat(ok, cases, repeats))
    return {"classes": classes, "fields": {k: f"{h} of {n}" for k, (h, n) in field_hits.items()}}
