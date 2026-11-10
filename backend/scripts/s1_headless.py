"""Headless S1 (PRD §15): CLI upload -> real settlement -> SETTLED, evidence saved.

Drives only the public API. It never approves, pays or writes the database: if the case needs a
human, it stops and says so (R11). Credentials come from the process environment, never argv.

  AEGIS_API_URL (default http://127.0.0.1:8000)  AEGIS_OPERATOR_ID  AEGIS_OPERATOR_PASSWORD
  python scripts/s1_headless.py --pdf S1.pdf --po PO-1042-<run> --invoice INV2-XXXX --out s1.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

S1_CENTS = 48000  # PRD §15: 100 x $4.80, 100 accepted
TERMINAL = {
    "SETTLED",
    "REJECTED",
    "DUPLICATE_BLOCKED",
    "FROZEN",
    "PAYOUT_FAILED",
    "NEEDS_MANUAL_REVIEW",
}


def verdict(case: dict) -> list[str]:
    """Return the reasons S1 is NOT proven; an empty list means every PRD §15 check holds."""
    problems = []
    if case.get("status") != "SETTLED":
        problems.append(f"status is {case.get('status')}, expected SETTLED")
    if case.get("duplicate_of_execution_id"):
        problems.append("case is a duplicate")
    paid = (case.get("po_consumption") or {}).get("paid_cents")
    if paid != S1_CENTS:
        problems.append(f"po paid_cents {paid} != {S1_CENTS}")
    steps = {e["status"]: e for e in case.get("settlement_timeline", [])}
    for needed in ("PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED", "SETTLED"):
        event = steps.get(needed)
        if event is None:
            problems.append(f"timeline missing {needed}")
        elif not event.get("verified") or event.get("simulated"):
            problems.append(f"{needed} not a verified, non-simulated event")
    if (case.get("paypal_invoice") or {}).get("status") not in ("PAID", "MARKED_AS_PAID"):
        problems.append("PayPal invoice not PAID/MARKED_AS_PAID")
    if not (case.get("audit_anchor") or {}).get("verified"):
        problems.append("audit anchor not verified")
    return problems


def run(args: argparse.Namespace) -> int:
    base = os.environ.get("AEGIS_API_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=base, timeout=30) as http:
        login = http.post(
            "/api/auth/login",
            json={
                "operator_id": os.environ["AEGIS_OPERATOR_ID"],
                "password": os.environ["AEGIS_OPERATOR_PASSWORD"],
            },
        )
        login.raise_for_status()
        # The session cookie is Secure; the jar would drop it over plain-http localhost.
        cookie = login.headers["set-cookie"].split("=", 1)[1].split(";")[0]
        headers = {"Cookie": f"aegis_session={cookie}", "X-CSRF-Token": login.json()["csrf_token"]}
        with Path(args.pdf).open("rb") as pdf:
            receipt = http.post(
                "/api/documents",
                headers=headers,
                files={"file": (Path(args.pdf).name, pdf, "application/pdf")},
                data={"po_id": args.po, "paypal_invoice_id": args.invoice},
            )
        receipt.raise_for_status()
        execution_id = receipt.json()["execution_id"]
        deadline = time.monotonic() + args.timeout
        case = {}
        while time.monotonic() < deadline:
            response = http.get(f"/api/executions/{execution_id}", headers=headers)
            response.raise_for_status()
            case = response.json()
            if case["status"] in TERMINAL:
                break
            time.sleep(2)
    problems = verdict(case)
    Path(args.out).write_text(
        json.dumps({"execution_id": execution_id, "case": case, "problems": problems}, indent=2)
    )
    sys.stdout.write(f"execution {execution_id}: {case.get('status')}\n")
    for problem in problems:
        sys.stdout.write(f"FAIL: {problem}\n")
    if not problems:
        sys.stdout.write(f"S1 PASS: evidence saved to {args.out}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--po", required=True)
    parser.add_argument("--invoice", required=True)
    parser.add_argument("--out", default="s1-evidence.json")
    parser.add_argument("--timeout", type=int, default=300)
    sys.exit(run(parser.parse_args()))
