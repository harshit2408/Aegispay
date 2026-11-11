# AegisPay evaluation results

## Local P2 integration — 2026-11-10

Implementation: `b4e4f5f`, branch `b/P2-07-phase-completion`. These are local replay and deterministic integration results, not live-provider accuracy or sandbox payout acceptance.

Environment: Linux Python 3.12 test container, PostgreSQL 16 with migrations through 0011. Backend mounted alone; no real environment files or credentials loaded. Full suite: **899 passed** in **147.39 s**, no skips; separately added restricted API-login startup test: **1 passed** in **8.24 s**. Frontend: **38 passed**, ESLint and production build passed. Harness: **26 passed**; Claude/Codex mirror check passed. Ruff check/format passed after normalizing the new startup test's final newline.

| Scenario / check | Actual result | Coverage / limit |
|---|---|---|
| S1 | AUTO_SETTLE, payable 48,000 cents | Replay ends APPROVED_FOR_PAYMENT; no payout |
| S2 | SHORT_PAY, payable 42,500 cents | Expected costs 6,592 cents versus 1,500 cents |
| S3 | FREEZE, risk 100, two eligible reviewers required | CFO rejection resumes through the durable checkpoint and reaches REJECTED |
| S3 detector disabled | FREEZE with HIGH_RISK and UNVERIFIED_SOURCE | No hidden-segment result is fabricated |
| S4 replay | Duplicate linked to S1 in **32.59 ms** | One local authenticated ASGI request, not a percentile |
| S4 document admission | Duplicate linked in **71.14 ms** | One local request through document admission |
| Authenticated SSE | Commit-to-HTTP notification **33.4 ms** | Real local TCP/Uvicorn connection; heartbeat and session revocation checked |
| Receiver / spend properties | 20 receiver examples + 20 reserve/release examples passed | Real PostgreSQL savepoints; no attacker hints changed frozen receiver; caps preserved |
| Accepted-value property | 25 generated examples passed | PO reservations never exceed accepted value |
| Restart recovery | Passed persisted approval/receipt and transient-node recovery tests | Approval IDs remain bound to original checkpoint lineage and gate hash |
| Vendor changes | Restricted-role mutation guards passed | Migration 0011 blocks arbitrary settlement edits to vendor trust roots |
| Normal API startup | Passed with restricted PostgreSQL login | Checkpoint setup and decision dispatcher started under API role |

Replay uses synthetic PDFs, recorded extraction/forensics evidence and execution-bound structured invoice recordings. It exercises authenticated routes, real PostgreSQL checkpoints, gate evaluation and resume. It does not measure extractor/classifier accuracy. Assertions verify zero spend-ledger, PO-ledger or outbox writes in replay.

## Remaining acceptance

Verification follow-up: merged Dev A's published security corrections (`1e0798d`, source `claude/fix-pr-40` through `cc4b98a`), including migration 0012 server-clock cooling timestamps, CFO-only settlement resume, idempotent halt alerts and expired-login-lockout reset. Preserved both JavaScript name-tree and OpenAction fixture coverage. The resulting local Linux/PostgreSQL suite passed **906 tests in 123.83 s** with Ruff passing. The preceding head `6923a7f` passed all five CI jobs in [run 42769525333](https://github.com/harshit2408/Aegispay/actions/runs/42769525333), including fresh Compose startup after fixing the reconciler's idle transaction. Fresh CI for the merged security corrections is reported separately; prior-head CI is not an exact-head claim.

- P2-13 remains partial: live demo reset returns HTTP 503 and still needs owner-seeder orchestration, new policy/PO/GRN trust roots and archival semantics; health still lacks complete token/webhook/account/worker/anchor probes from PRD §14.
- P2-15 remains open: run S1–S4 against owner-configured PayPal sandbox through confirmed payout and invoice settlement. Browser Playwright S1–S4 is also unexecuted.
- No live LLM/provider call, sandbox payout, hosted Render deployment or independent Dev A review was performed. R1–R4 changes require that review before merge.

## A7 Treasury Analyst boundary — 2026-11-12

Local Linux Python 3.12 checks passed **15 tests in 2.83 s**: five synthetic boundary eval cases plus ten selector/governance unit cases. The schema rejects arbitrary SQL/tools, unknown statuses, unsupported time windows and boolean/fractional days; the prompt is delimited and contains no execution records. These injected completions establish server boundaries, not natural-language selection accuracy. Four PostgreSQL route cases are verified separately by the integrated suite; no passing result is claimed here before that run. Frontend **58 tests** passed, including five Agent Framework runner/client checks for zero tools, stale-result clearing, cancellation, CSRF and provider errors. ESLint and TypeScript passed; two unused-variable warnings in the test were then removed. No live model call, hosted Studio session or model-quality measurement was performed.
