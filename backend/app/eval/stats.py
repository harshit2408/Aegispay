"""Pure statistics helpers. Floats are fine here: no money."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import sqrt
from statistics import pvariance


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError("successes must be within 0..n")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def per_class_summary(results: Mapping[str, Sequence[Sequence[bool]]]) -> dict[str, dict]:
    """results: class -> repeated runs -> per-case pass/fail. Trials are pooled for N of M."""
    out: dict[str, dict] = {}
    for cls, runs in results.items():
        per_run = [sum(r) for r in runs]
        m = sum(len(r) for r in runs)
        k = sum(per_run)
        lo, hi = wilson_interval(k, m)
        rates = [sum(r) / len(r) for r in runs if r]
        out[cls] = {
            "n_of_m": f"{k} of {m}",
            "successes": k,
            "trials": m,
            "runs": len(runs),
            "wilson95": (lo, hi),
            "run_successes": per_run,
            "run_variance": pvariance(rates) if len(rates) > 1 else 0.0,
        }
    return out
