"""S1 preflight: are all inputs for a real sandbox S1 present? Prints variable NAMES only.

  python scripts/s1_preflight.py --env-file ../.env.s1     (or export the variables, no flag)
  ... --env-file ../.env.s1 --env-file ../.env.s1.operators --after-init   (after s1_init_secrets)

The env-file is parsed here, in-process; values are never printed, logged or put in argv.
Process environment overrides the file, as `docker compose` does. Exit 0 = ready, 1 = not.
Stdlib only, so it runs on the host before any image exists.
"""

import argparse
import importlib.util
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

# Single source of truth for reviewed (provider, model); stdlib-only file, loaded by path.
_ALLOWLIST = Path(__file__).resolve().parents[1] / "app" / "agents" / "llm_allowlist.py"

NONBLANK = (
    "TREASURY_CLIENT_ID",
    "TREASURY_CLIENT_SECRET",
    "TREASURY_ACCOUNT_EMAIL",
    "VENDOR_CLIENT_ID",
    "VENDOR_CLIENT_SECRET",
    "VENDOR_ACCOUNT_EMAIL",
    "VENDOR_PAYER_ID",
    "LLM_API_KEY",
    "AUDIT_ANCHOR_REPO",
    "AUDIT_ANCHOR_TOKEN",
    "POSTGRES_PASSWORD",
)
MIN_32 = ("AUDIT_HMAC_KEY", "SESSION_SECRET")  # config.py: min_length=32
OPTIONAL = ("TREASURY_WEBHOOK_ID", "VENDOR_WEBHOOK_ID")
# Written by s1_init_secrets.py (.env.s1.operators); only required with --after-init.
GENERATED = (
    "S1_API_DB_PASSWORD",
    "S1_SETTLE_DB_PASSWORD",
    "S1_OPERATOR_PASSWORD_CONTROLLER1",
    "S1_OPERATOR_PASSWORD_CONTROLLER2",
    "S1_OPERATOR_PASSWORD_CFO",
)
SANDBOX_URLS = ("https://api-m.sandbox.paypal.com", "https://api.sandbox.paypal.com")


def parse_env_file(text: str) -> dict[str, str]:
    values = {}
    for raw in text.splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        quoted = re.fullmatch(r"([\"'])(.*?)\1\s*(?:#.*)?", value)
        if quoted:
            value = quoted.group(2)
        elif value.startswith("#"):
            value = ""  # `KEY=   # note` is blank to docker compose
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name.strip()] = value
    return values


def problems(env: Mapping[str, str], *, after_init: bool = False) -> list[str]:
    """Return 'NAME: reason' lines; reasons never include a value."""
    required = NONBLANK + (GENERATED if after_init else ())
    found = [f"{n}: missing or blank" for n in required if not env.get(n, "").strip()]
    for name in MIN_32:
        if len(env.get(name, "")) < 32:
            found.append(f"{name}: missing or shorter than 32 characters")
    if env.get("DEMO_ENABLED", "").strip().lower() != "true":
        found.append("DEMO_ENABLED: must be true")
    if env.get("DEMO_REPLAY_MODE", "false").strip().lower() != "false":
        found.append("DEMO_REPLAY_MODE: must be false (replay is not sandbox evidence)")
    if env.get("PAYPAL_BASE_URL", SANDBOX_URLS[0]).strip().rstrip("/") not in SANDBOX_URLS:
        found.append("PAYPAL_BASE_URL: must be the PayPal sandbox origin")
    if env.get("LLM_API_KEY", "").strip():
        try:
            spec = importlib.util.spec_from_file_location("llm_allowlist", _ALLOWLIST)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            provider, _ = module.select(env)
            if not module.key_matches(provider, env["LLM_API_KEY"].strip()):
                found.append("LLM_PROVIDER: LLM_API_KEY is not a key for this provider")
        except ValueError:
            found.append("LLM_PROVIDER: provider/model not on the reviewed allowlist")
        except (OSError, AttributeError):
            found.append("LLM_PROVIDER: allowlist file unreadable, cannot verify provider")
    treasury, vendor = env.get("TREASURY_ACCOUNT_EMAIL", ""), env.get("VENDOR_ACCOUNT_EMAIL", "")
    if treasury and treasury.casefold() == vendor.casefold():
        found.append("VENDOR_ACCOUNT_EMAIL: must differ from TREASURY_ACCOUNT_EMAIL")
    return found


def load(env_files: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for env_file in env_files:  # later files win, like repeated `docker compose --env-file`
        values.update(parse_env_file(env_file.read_text(encoding="utf-8")))
    values.update(os.environ)
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument(
        "--after-init", action="store_true", help="also require the s1_init_secrets.py outputs"
    )
    args = parser.parse_args(argv)
    env = load(args.env_file)
    issues = problems(env, after_init=args.after_init)
    for line in issues:
        sys.stdout.write(f"MISSING {line}\n")
    for name in OPTIONAL:
        if not env.get(name, "").strip():
            sys.stdout.write(f"optional, unset: {name}\n")
    sys.stdout.write("S1 preflight FAILED\n" if issues else "S1 preflight OK\n")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
