import importlib.util
import json
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from app.demo.seeder import OPERATORS, DemoSeed

spec = importlib.util.spec_from_file_location(
    "s1_init_secrets", Path(__file__).parents[2] / "scripts" / "s1_init_secrets.py"
)
init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init)

IDENTITY = {
    "VENDOR_PAYER_ID": "ABCDEFGHJ2345",
    "VENDOR_ACCOUNT_EMAIL": "seller@example.com",
    "TREASURY_ACCOUNT_EMAIL": "buyer@example.com",
}


@pytest.fixture
def identity(monkeypatch):
    for name, value in IDENTITY.items():
        monkeypatch.setenv(name, value)


def run(tmp_path):
    return init.main(
        [
            "--run",
            "s1a",
            "--onboarding-reference",
            "order X; private record",
            "--out-dir",
            str(tmp_path),
        ]
    )


def test_writes_valid_seed_and_matching_passwords(tmp_path, identity, capsys):
    assert run(tmp_path) == 0
    seed = json.loads((tmp_path / init.SEED_FILE).read_text())
    config = DemoSeed.model_validate(seed)  # what `python -m app.demo` will accept
    assert config.vendor_payer_id == IDENTITY["VENDOR_PAYER_ID"] and set(
        seed["operator_hashes"]
    ) == set(OPERATORS)
    env = dict(line.split("=", 1) for line in (tmp_path / init.ENV_FILE).read_text().splitlines())
    assert PasswordHasher().verify(
        seed["operator_hashes"]["controller1"], env["S1_OPERATOR_PASSWORD_CONTROLLER1"]
    )
    assert env["S1_API_DB_PASSWORD"] != env["S1_SETTLE_DB_PASSWORD"]
    out = capsys.readouterr().out
    assert all(value not in out for value in env.values())  # names only on stdout
    assert "S1_API_DB_PASSWORD" in out


def test_never_overwrites_existing_secrets(tmp_path, identity, capsys):
    (tmp_path / init.ENV_FILE).write_text("keep\n")
    assert run(tmp_path) == 1
    assert (tmp_path / init.ENV_FILE).read_text() == "keep\n"
    assert not (tmp_path / init.SEED_FILE).exists()
    assert "REFUSED" in capsys.readouterr().out


def test_missing_identity_names_are_reported_without_writing(tmp_path, monkeypatch, capsys):
    for name in IDENTITY:
        monkeypatch.delenv(name, raising=False)
    assert run(tmp_path) == 1
    assert (
        "MISSING VENDOR_PAYER_ID, VENDOR_ACCOUNT_EMAIL, TREASURY_ACCOUNT_EMAIL"
        in capsys.readouterr().out
    )
    assert list(tmp_path.iterdir()) == []


def test_malformed_payer_id_fails_before_any_write(tmp_path, identity, monkeypatch):
    monkeypatch.setenv("VENDOR_PAYER_ID", "not-a-payer-id")
    with pytest.raises(ValueError):
        run(tmp_path)
    assert list(tmp_path.iterdir()) == []
