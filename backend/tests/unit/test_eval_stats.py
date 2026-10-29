import pytest

from app.eval.stats import per_class_summary, wilson_interval


def test_wilson_n_zero_is_safe():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_known_value():
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)


def test_wilson_edges_bounded():
    assert wilson_interval(0, 5)[0] == 0.0
    assert wilson_interval(5, 5)[1] == 1.0
    with pytest.raises(ValueError):
        wilson_interval(6, 5)


def test_per_class_summary_n_of_m_and_variance():
    s = per_class_summary({"a": [[True, True], [True, False]], "b": [[False]]})
    assert s["a"]["n_of_m"] == "3 of 4"
    assert s["a"]["run_successes"] == [2, 1]
    assert s["a"]["run_variance"] == pytest.approx(0.0625)
    assert s["b"]["run_variance"] == 0.0
    lo, hi = s["a"]["wilson95"]
    assert 0 < lo < 0.75 < hi < 1
