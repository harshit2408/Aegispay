import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "s1_preflight", Path(__file__).parents[2] / "scripts" / "s1_preflight.py"
)
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)

SENTINEL = "sentinel-value-must-never-print-0123456789"
GOOD = {name: SENTINEL for name in (*pre.NONBLANK, *pre.MIN_32)} | {
    "TREASURY_ACCOUNT_EMAIL": "buyer@example.com",
    "VENDOR_ACCOUNT_EMAIL": "seller@example.com",
    "DEMO_ENABLED": "true",
    "DEMO_REPLAY_MODE": "false",
}


def test_complete_environment_passes():
    assert pre.problems(GOOD) == []


@pytest.mark.parametrize("name", [*pre.NONBLANK, *pre.MIN_32, "DEMO_ENABLED"])
def test_each_missing_name_is_reported(name):
    env = {k: v for k, v in GOOD.items() if k != name}
    assert [p.split(":")[0] for p in pre.problems(env)] == [name]


def test_blank_and_short_values_fail():
    env = GOOD | {"LLM_API_KEY": "   ", "SESSION_SECRET": "x" * 31}
    assert {p.split(":")[0] for p in pre.problems(env)} == {"LLM_API_KEY", "SESSION_SECRET"}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DEMO_ENABLED", "false"),
        ("DEMO_REPLAY_MODE", "true"),
        ("PAYPAL_BASE_URL", "https://api-m.paypal.com"),
        ("VENDOR_ACCOUNT_EMAIL", "BUYER@example.com"),
    ],
)
def test_unsafe_settings_fail(name, value):
    assert [p.split(":")[0] for p in pre.problems(GOOD | {name: value})] == [name]


def test_sandbox_url_and_unset_replay_are_accepted():
    assert pre.problems({k: v for k, v in GOOD.items() if k != "DEMO_REPLAY_MODE"}) == []
    assert pre.problems(GOOD | {"PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com/"}) == []


def test_env_file_parsing_handles_quotes_comments_export():
    text = "# c\nA=1\nexport B='two'\nC=\"three\" \nD=four # note\nbad line\nE=\n"
    assert pre.parse_env_file(text) == {"A": "1", "B": "two", "C": "three", "D": "four", "E": ""}


def test_cli_prints_names_never_values(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "s1.env"
    env_file.write_text(f"TREASURY_CLIENT_SECRET={SENTINEL}\nLLM_API_KEY=\n", encoding="utf-8")
    for name in (*pre.NONBLANK, *pre.MIN_32, "DEMO_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert pre.main(["--env-file", str(env_file)]) == 1
    out = capsys.readouterr().out
    assert "MISSING LLM_API_KEY" in out and "MISSING TREASURY_CLIENT_ID" in out
    assert "TREASURY_CLIENT_SECRET" not in out and SENTINEL not in out
    assert out.rstrip().endswith("S1 preflight FAILED")


def test_process_environment_overrides_file(tmp_path, monkeypatch):
    env_file = tmp_path / "s1.env"
    env_file.write_text("LLM_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", SENTINEL)
    assert pre.load([env_file])["LLM_API_KEY"] == SENTINEL


def test_after_init_requires_generated_names_and_later_file_wins(tmp_path, monkeypatch):
    assert [p.split(":")[0] for p in pre.problems(GOOD, after_init=True)] == list(pre.GENERATED)
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    first.write_text("X_TEST_NAME=one\n")
    second.write_text("X_TEST_NAME=two\n")
    monkeypatch.delenv("X_TEST_NAME", raising=False)
    assert pre.load([first, second])["X_TEST_NAME"] == "two"


def test_blank_value_with_inline_comment_is_blank():
    # compose reads `KEY=   # note` as empty; so must the preflight (false-positive regression).
    text = (
        "A=      # USER: paste it\nB=# glued\nC=\"x # y\"\nD='p q' # note\nE=real # note\nF=a#b\n"
    )
    assert pre.parse_env_file(text) == {
        "A": "",
        "B": "",
        "C": "x # y",
        "D": "p q",
        "E": "real",
        "F": "a#b",
    }


def test_commented_blank_key_fails_the_preflight(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "s1.env"
    env_file.write_text("LLM_API_KEY=      # USER: paste\n", encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert pre.main(["--env-file", str(env_file)]) == 1
    assert "MISSING LLM_API_KEY" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra",
    [
        {"LLM_PROVIDER": "evil"},
        {"LLM_PROVIDER": "openrouter", "LLM_MODEL_EXTRACT": "gpt-6.1-sol"},
        {"LLM_PROVIDER": "openai", "LLM_MODEL_REASON": "some/other:free"},
        {"LLM_MODEL_EXTRACT": "x", "LLM_MODEL_REASON": "y"},
    ],
)
def test_unreviewed_llm_selection_fails_when_key_is_set(extra):
    assert [p.split(":")[0] for p in pre.problems(GOOD | extra)] == ["LLM_PROVIDER"]


def test_reviewed_llm_selections_pass_and_blank_key_skips_the_llm_check():
    assert pre.problems(GOOD | {"LLM_PROVIDER": "openrouter"}) == []
    assert pre.problems(GOOD | {"LLM_PROVIDER": "openai", "LLM_MODEL_EXTRACT": ""}) == []
    # no key: only the key itself is reported, not a second provider complaint
    env = GOOD | {"LLM_PROVIDER": "evil", "LLM_API_KEY": ""}
    assert [p.split(":")[0] for p in pre.problems(env)] == ["LLM_API_KEY"]
