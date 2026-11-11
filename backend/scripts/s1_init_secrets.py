"""Generate S1 operator passwords, the Argon2id seed file and runtime DB passwords (names only).

  VENDOR_PAYER_ID=... VENDOR_ACCOUNT_EMAIL=... TREASURY_ACCOUNT_EMAIL=... \
  python scripts/s1_init_secrets.py --run s1a --onboarding-reference "<order id + private record>" \
      --out-dir /out

Writes two git-ignored files and refuses to overwrite either (rerun = delete them yourself):
  operator-seed-s1.json   identity + Argon2id hashes, input for `python -m app.demo`
  .env.s1.operators       plaintext operator passwords and the two runtime DB login passwords
The payer ID comes from the environment, never argv, and is never read from a PDF or invoice (R1).
"""

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path

from argon2 import PasswordHasher

from app.demo.seeder import OPERATORS, DemoSeed

SEED_FILE = "operator-seed-s1.json"
ENV_FILE = ".env.s1.operators"
ONBOARDING_METHOD = "vendor-token unapproved order payee.merchant_id (SPIKE-10)"


def build(*, run: str, reference: str, env: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Return (seed json, secret env values). Raises on an invalid identity before any write."""
    hasher = PasswordHasher()
    passwords = {name: secrets.token_urlsafe(24) for name in OPERATORS}
    seed = {
        "run_id": run,
        "vendor_payer_id": env["VENDOR_PAYER_ID"],
        "vendor_email": env["VENDOR_ACCOUNT_EMAIL"],
        "treasury_email": env["TREASURY_ACCOUNT_EMAIL"],
        "onboarding_evidence": {"method": ONBOARDING_METHOD, "reference": reference},
        "operator_hashes": {name: hasher.hash(pw) for name, pw in passwords.items()},
    }
    DemoSeed.model_validate(seed)  # same validation the seeder applies
    secret_env = {f"S1_OPERATOR_PASSWORD_{name.upper()}": pw for name, pw in passwords.items()}
    # Hex, not base64: these go into postgresql:// URLs.
    secret_env["S1_API_DB_PASSWORD"] = secrets.token_hex(24)
    secret_env["S1_SETTLE_DB_PASSWORD"] = secrets.token_hex(24)
    return seed, secret_env


def write_private(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as handle:  # "x": never clobber existing secrets
        handle.write(text)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--onboarding-reference", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    missing = [
        n
        for n in ("VENDOR_PAYER_ID", "VENDOR_ACCOUNT_EMAIL", "TREASURY_ACCOUNT_EMAIL")
        if not os.environ.get(n, "").strip()
    ]
    if missing:
        sys.stdout.write("MISSING " + ", ".join(missing) + "\n")
        return 1
    targets = [args.out_dir / SEED_FILE, args.out_dir / ENV_FILE]
    if any(t.exists() for t in targets):
        sys.stdout.write("REFUSED: output file exists; delete it deliberately to regenerate\n")
        return 1
    seed, secret_env = build(
        run=args.run, reference=args.onboarding_reference, env=dict(os.environ)
    )
    write_private(targets[0], json.dumps(seed, indent=2) + "\n")
    write_private(targets[1], "".join(f"{k}={v}\n" for k, v in secret_env.items()))
    sys.stdout.write(f"wrote {targets[0]}\nwrote {targets[1]}\n")
    sys.stdout.write("variables: " + ", ".join(secret_env) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
