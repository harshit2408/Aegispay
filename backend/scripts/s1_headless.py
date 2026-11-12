"""Headless S1/S2/S4 (PRD §15): CLI upload -> settlement -> SETTLED (S4: blocked), evidence saved.

Drives only the public API. It never approves, pays or writes the database: if the case needs a
human, it stops and says so (R11). Credentials come from the process environment, never argv.

  AEGIS_API_URL (default http://127.0.0.1:8000)  AEGIS_OPERATOR_ID  AEGIS_OPERATOR_PASSWORD
  python scripts/s1_headless.py --pdf S1.pdf --po PO-1042-<run> --invoice INV2-XXXX --out s1.json
  ... --scenario S2 --pdf S2.pdf --po PO-1043-<run> --invoice INV2-YYYY   (short-pay)
  ... --scenario S4 --pdf S1.pdf --po PO-1042-<run> --invoice <S1 invoice> --original <S1 exec id>
  ... --scenario S3 --pdf S3.pdf --po PO-1044-<run> --invoice <S3 invoice>   (attack: FROZEN)
S3 stops at FROZEN. Only if AEGIS_REVIEWER_ID/AEGIS_REVIEWER_PASSWORD (a second real operator, never
the uploader) are in the environment does it send REJECT. It never sends APPROVE or SHORT_PAY (R11).
"""

import argparse
import json
import os
import sys
import time
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx

S1_CENTS = 48000  # PRD §15: 100 x $4.80, 100 accepted
# PRD §15/§12.3: S2 bills 100 x $5.00, 85 accepted: pays 42500; expected cost 6592 vs 1500.
SETTLED_SCENARIOS = {
    # S1's AUTO_SETTLE route is recorded in the evidence; its verdict keeps the original checks.
    "S1": {"cents": S1_CENTS, "route": None, "invoice": ("PAID", "MARKED_AS_PAID")},
    "S2": {
        "cents": 42500,
        "route": "SHORT_PAY",
        "invoice": ("PARTIALLY_PAID",),
        "expected_cost": (6592, 1500),
    },
}
DUPLICATE_BUDGET_SECONDS = 1.0  # PRD §15 S4
TERMINAL = {
    "SETTLED",
    "REJECTED",
    "DUPLICATE_BLOCKED",
    "FROZEN",
    "PAYOUT_FAILED",
    "NEEDS_MANUAL_REVIEW",
}


def api_base(url: str) -> str:
    """The session cookie is re-sent by hand, so refuse cleartext HTTP except to loopback."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname:
        try:
            if parts.hostname == "localhost" or ip_address(parts.hostname).is_loopback:
                return url
        except ValueError:
            pass
    raise SystemExit(f"AEGIS_API_URL must be https, or http to loopback only: {url!r}")


def verdict(case: dict, scenario: str = "S1") -> list[str]:
    """Reasons the scenario is NOT proven; an empty list means every PRD §15 check holds."""
    want = SETTLED_SCENARIOS[scenario]
    cents = want["cents"]
    problems = []
    if want["route"] and (case.get("gate") or {}).get("route") != want["route"]:
        problems.append(
            f"gate route is {(case.get('gate') or {}).get('route')}, expected {want['route']}"
        )
    if "expected_cost" in want:
        plan = case.get("short_pay") or {}
        got = (
            plan.get("expected_cost_pay_and_claim_cents"),
            plan.get("expected_cost_short_pay_cents"),
        )
        if got != want["expected_cost"] or plan.get("chosen") != "SHORT_PAY":
            problems.append(
                f"short-pay plan {got} / {plan.get('chosen')}, expected {want['expected_cost']}"
            )
    if case.get("status") != "SETTLED":
        problems.append(f"status is {case.get('status')}, expected SETTLED")
    if case.get("duplicate_of_execution_id"):
        problems.append("case is a duplicate")
    consumption = case.get("po_consumption") or {}
    if consumption.get("paid_cents") != cents:
        problems.append(f"po paid_cents {consumption.get('paid_cents')} != {cents}")
    # R17: PO closes only when everything accepted is paid and nothing is still reserved.
    if consumption.get("accepted_value_cents") != cents or consumption.get("reserved_cents"):
        problems.append("PO not fully consumed (accepted value != paid, or cents still reserved)")
    steps = {e["status"]: e for e in case.get("settlement_timeline", [])}
    for needed in ("PAYOUT_SUBMITTED", "PAYOUT_SUCCEEDED", "SETTLED"):
        event = steps.get(needed)
        if event is None:
            problems.append(f"timeline missing {needed}")
        elif not event.get("verified") or event.get("simulated"):
            problems.append(f"{needed} not a verified, non-simulated event")
    if (case.get("paypal_invoice") or {}).get("status") not in want["invoice"]:
        problems.append(f"PayPal invoice not {'/'.join(want['invoice'])}")
    if not (case.get("audit_anchor") or {}).get("verified"):
        problems.append("audit anchor not verified")
    return problems


def verdict_duplicate(case: dict, original: str, elapsed: float) -> list[str]:
    """S4 (PRD §15): blocked within one second, linked to the original, no payout started."""
    problems = []
    if case.get("status") != "DUPLICATE_BLOCKED":
        problems.append(f"status is {case.get('status')}, expected DUPLICATE_BLOCKED")
    if case.get("duplicate_of_execution_id") != original:
        problems.append("duplicate is not linked to the original execution")
    if elapsed >= DUPLICATE_BUDGET_SECONDS:
        problems.append(f"blocked in {elapsed:.3f}s, budget is {DUPLICATE_BUDGET_SECONDS}s")
    if case.get("settlement_timeline"):
        problems.append("a duplicate must have no settlement events")
    return problems


def verdict_frozen(case: dict) -> list[str]:
    """S3 stage 1 (PRD §15): the attack is frozen with dual approval, nothing is paid."""
    problems = []
    gate = case.get("gate") or {}
    if case.get("status") != "FROZEN" or gate.get("route") != "FREEZE":
        problems.append(
            f"status/route {case.get('status')}/{gate.get('route')}, expected FROZEN/FREEZE"
        )
    if gate.get("required_approvals") != 2:
        problems.append(f"required_approvals {gate.get('required_approvals')}, expected 2")
    if (case.get("risk") or {}).get("score", 0) < 80:
        problems.append("risk score below the dual-approval threshold")
    forensics = case.get("forensics") or {}
    if not forensics.get("hidden_segments") and forensics.get("classifier_label") == "CLEAN":
        problems.append("neither hidden text nor an injection label was detected")
    if case.get("settlement_timeline") or (case.get("po_consumption") or {}).get("paid_cents"):
        problems.append("money moved on a frozen case")
    return problems


def verdict_rejected(case: dict, reviewer: str) -> list[str]:
    """S3 stage 2: a second real operator rejected it through the public API."""
    problems = []
    if case.get("status") != "REJECTED":
        problems.append(f"status is {case.get('status')}, expected REJECTED")
    votes = [(a["actor"]["operator_id"], a["action"]) for a in case.get("approvals", [])]
    if (reviewer, "REJECT") not in votes:
        problems.append("no REJECT approval recorded for the reviewer")
    if case.get("settlement_timeline") or (case.get("po_consumption") or {}).get("paid_cents"):
        problems.append("money moved on a rejected case")
    return problems


def _session(http: httpx.Client, operator_id: str, password: str) -> dict:
    login = http.post("/api/auth/login", json={"operator_id": operator_id, "password": password})
    login.raise_for_status()
    # The session cookie is Secure; the jar would drop it over plain-http localhost.
    cookie = login.cookies.get("aegis_session")
    if not cookie:
        raise SystemExit("login response carried no aegis_session cookie")
    return {"Cookie": f"aegis_session={cookie}", "X-CSRF-Token": login.json()["csrf_token"]}


def _reject(http: httpx.Client, execution_id: str, case: dict, reviewer: dict) -> None:
    """The only write besides upload: REJECT (the safe direction) as the second operator."""
    response = http.post(
        f"/api/executions/{execution_id}/decisions",
        headers=reviewer,
        json={
            "action": "REJECT",
            "reason": "S3 sandbox run: look-alike payee and hidden instructions in the PDF.",
            "gate_hash": case["gate"]["gate_hash"],
        },
    )
    response.raise_for_status()


def run(args: argparse.Namespace) -> int:
    scenario = getattr(args, "scenario", "S1")
    reviewer_id = os.environ.get("AEGIS_REVIEWER_ID")

    def check(case: dict) -> list[str]:
        if scenario == "S4":
            return verdict_duplicate(case, args.original, elapsed)
        if scenario == "S3":
            return verdict_frozen(case)
        return verdict(case, scenario)

    elapsed = 0.0
    base = api_base(os.environ.get("AEGIS_API_URL", "http://127.0.0.1:8000"))
    with httpx.Client(base_url=base, timeout=30) as http:
        headers = _session(
            http, os.environ["AEGIS_OPERATOR_ID"], os.environ["AEGIS_OPERATOR_PASSWORD"]
        )
        started = time.monotonic()  # after login: Argon2 time is not the duplicate-check latency
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
        terminal_at = None
        case = {}
        while time.monotonic() < deadline:
            response = http.get(f"/api/executions/{execution_id}", headers=headers)
            response.raise_for_status()
            case = response.json()
            elapsed = time.monotonic() - started
            if case["status"] in TERMINAL:
                # Audit events and the anchor receipt land a moment after the status flips.
                terminal_at = terminal_at or time.monotonic()
                if not check(case) or time.monotonic() - terminal_at > args.grace:
                    break
            time.sleep(2)
        frozen = case
        if scenario == "S3" and reviewer_id and not check(case):
            reviewer = _session(http, reviewer_id, os.environ["AEGIS_REVIEWER_PASSWORD"])
            _reject(http, execution_id, case, reviewer)
            for _ in range(max(1, args.timeout // 2)):
                case = http.get(f"/api/executions/{execution_id}", headers=headers).json()
                if case["status"] == "REJECTED":
                    break
                time.sleep(2)
    problems = check(frozen)
    if scenario == "S3" and reviewer_id and not problems:
        problems = verdict_rejected(case, reviewer_id)
    if case.get("status") == "FROZEN" and not (scenario == "S3" and not problems):
        sys.stdout.write("Needs human approval (R11); this CLI never approves. Stopping.\n")
    Path(args.out).write_text(
        json.dumps(
            {
                "scenario": scenario,
                "execution_id": execution_id,
                "elapsed_seconds": round(elapsed, 3),
                "frozen_case": frozen if scenario == "S3" else None,
                "case": case,
                "problems": problems,
            },
            indent=2,
        )
    )
    sys.stdout.write(f"execution {execution_id}: {case.get('status')}\n")
    for problem in problems:
        sys.stdout.write(f"FAIL: {problem}\n")
    if not problems:
        sys.stdout.write(f"{scenario} PASS: evidence saved to {args.out}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", choices=("S1", "S2", "S3", "S4"), default="S1")
    parser.add_argument("--original", help="S4: execution id of the settled original")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--po", required=True)
    parser.add_argument("--invoice", required=True)
    parser.add_argument("--out", default="s1-evidence.json")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--grace", type=int, default=60, help="seconds to wait for audit/anchor")
    parsed = parser.parse_args()
    if parsed.scenario == "S4" and not parsed.original:
        parser.error("--scenario S4 requires --original")
    sys.exit(run(parsed))
