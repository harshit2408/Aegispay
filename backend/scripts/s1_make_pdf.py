"""Build a PRD §15 invoice PDF: S1 100 x LiDAR bracket @ $4.80, S2 100 x servo @ $5.00, S3 attack.

  python scripts/s1_make_pdf.py --run s1a --payee-email supplier@sandbox.example --out S1.pdf

The invoice number and PO follow the seeder's naming (`AP-<1042|1043>-<run>`, `PO-...`).
S1/S2 have no hidden text; S3 has a look-alike payee and white-on-white text. The payee email
is only text on the page: the payee that gets paid always comes from vendor master (R1).
"""

import argparse
import os
import sys
from pathlib import Path

from app.demo.pdf import scenario_pdf

SCENARIOS = ("S1", "S2", "S3")


def build(path: Path, *, run: str, payee_email: str, scenario: str = "S1") -> None:
    path.write_bytes(scenario_pdf(run=run, payee_email=payee_email, scenario=scenario))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="S1")
    parser.add_argument("--payee-email", default=os.environ.get("VENDOR_ACCOUNT_EMAIL"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.payee_email:
        parser.error("--payee-email or VENDOR_ACCOUNT_EMAIL is required")
    build(args.out, run=args.run, payee_email=args.payee_email, scenario=args.scenario)
    sys.stdout.write(f"wrote {args.out}\n")
