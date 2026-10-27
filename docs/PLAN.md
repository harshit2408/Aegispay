---
tags: [aegispay, plan]
source: "[[PRD_SRS]] v2.1 §19"
---
# AegisPay — Delivery Plan

Spec: [[PRD_SRS]] (single source of truth). State: [[CHECKLIST]]. Daily log: [[SESSION_LOG]]. Map: `maps/AegisPay.canvas`.

**Deadline:** 2026-11-12 12:00 PT (01:30 IST Nov 13). **We submit Nov 11.** Judging Dec 1–15 — demo and licences must live until Dec 15.

## Who owns what

| | Dev A | Dev B |
|---|---|---|
| Area | PayPal (both apps), settlement + spend ledger + reconciler, webhooks, LangGraph + AI agents, forensics, policy gate, auth, evals | Full stack + data: backend skeleton, DB schema + migrations, seeder, audit chain, match + duplicates (R9), docker-compose, Render deploy; frontend, AG Studio cockpit + widgets, vendor portal, CI, user research, video |
| Main thread agent | Claude Code: `claude --agent ponytail-backend` or `ponytail-ai` | Codex: `AGENTS.md`; frontend/backend ponytail guidance by task (see `harness/CODEX.md`) |
| Owns dirs | `backend/app/{paypal,settlement,decision,forensics,agents,routers,ops}/`, `docs/adr/`, `docs/EVAL.md`, `docs/TOOLS.md`, `docs/SANDBOX_SETUP.md` | `frontend/`, `backend/app/{models,db.py,config.py,audit,demo}/`, `backend/migrations/`, `docker-compose.yml`, `.github/`, `render.yaml`, `docs/USER_RESEARCH.md` |
| Shared | `docs/CHECKLIST.md`, `docs/SESSION_LOG.md`, `docs/PRD_SRS.md`, `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.codex/`, `.agents/`, `.ecc/memory/team/` | same |

Touching the other developer's directory: allowed for small fixes, but say so in the commit message and the session log.

Session exception (user-authorized 2026-10-25): Dev B owns P0-14 sandbox webhook setup and SPIKE-8 verification. P1-09 application webhook receiver and deduplication remain with Dev A.

## Phases (detail and IDs in [[CHECKLIST]])

| Phase | Dates | Exit criterion | Critical items |
|---|---|---|---|
| P0 Setup + spikes | Oct 21–22 | Spike table filled; go/no-go per assumption | SPIKE-1…11 (10–11 added by PRD v2.1), LICENSE, CI, Render |
| P1 Foundations | Oct 23–27 | **Headless S1**: CLI upload → sandbox payout → invoice fully paid (`MARKED_AS_PAID`) | v2.1 schema (operators, vendor changes, PO ledger), PayPal clients, write-ahead SettlementService in its own worker, transition table, reconciler, webhooks; app shell |
| P2 Decision engine | Oct 28 – Nov 2 | **Headless S1–S4 pass**; first EVAL numbers | forensics, match, risk, gate G1–G14 with `gate_hash`-bound approvals, vendor change control, GRN route, halt + velocity, HMAC audit, property tests; ledger/gauge/exposure widgets |
| P3 Agents + deploy | Nov 3–6 | **Hosted S1–S4 green on Render**; rough cut Nov 5 | Investigator, auth + sessions/CSRF, parser sandbox, demo isolation, deploy (api + settlement worker) by Nov 4; decision-path + settlement widgets, ops panel |
| P4 Hardening | Nov 7–8 | Feature freeze Nov 8 EOD | crash-injection, webhook-forgery, SoD role-matrix, chaos + contract tests, Playwright |
| P5 Ship | Nov 9–11 | Submitted | README, TOOLS, EVAL, video, clean-clone check |

## The interface contract between A and B

Dev B must never wait on Dev A, so the seam is fixed early and mocked:

1. **OpenAPI** — PRD §13 routes. Dev A publishes `backend/openapi.json` by end of Oct 24 (stubs are fine). Dev B generates `frontend/src/lib/api.ts` from it and builds against a mock until P2.
2. **SSE event schema** — `{type: "node"|"settlement"|"webhook", execution_id, name, status, ts}`. No evidence content on the stream (R25); the UI fetches details over REST. Settlement events arrive via Postgres `LISTEN/NOTIFY` (ADR-007). Agree by Oct 27; any change is a commit touching both sides plus a team memory entry.
3. **Execution status enum** — exactly PRD §8 `invoice_executions.status`, which v2.1 extends with `PAYOUT_SENDING` and `NEEDS_MANUAL_REVIEW`; allowed transitions are the PRD §8 table. No new statuses without updating PRD §8.
4. **Decision contract** — `POST /api/executions/{id}/decisions` takes `{action, reason}` plus the `gate_hash` the UI displayed; a stale hash returns 409 and the case view reloads (R11). Roles are the five in PRD §13 / R18.

A change to any of these three = write an ADR or memory `decision` and tell the other dev in [[SESSION_LOG]].

## Daily sync rhythm

| When | What | Mechanism |
|---|---|---|
| Session start | Pull, see progress + your next items + last handoff | `SessionStart` hook (automatic) → `/standup` for detail |
| Before starting an item | Claim it: `(WIP A)` on the checklist line, push | manual, 10 s |
| After every test run | Pull --rebase; push committed work; agent told to commit if dirty | `post-test-sync` hook (automatic) |
| End of session | Tick items, append handoff, save decisions to team memory, push | `/handoff` (memory-keeper agent) |
| Weekly (Mon) | Check phase exit criteria; apply cut order if behind | both devs, 15 min |

## Cut order if behind (PRD §19)
1. P1 negotiation agent → 2. Studio Agent Framework agent (keep custom widgets) → 3. S5 → 4. React Flow → stepper → 5. OCR-diff detector.

**Never cut:** payee lock, async settlement, duplicate check, operator auth, LICENSE + README + judge guide, the video, and the v2.1 controls listed in PRD §19 (R5, R11, R13, R15–R20, R24, R25). R21 anchor, R22 parser limits and R23 alert webhook may degrade only with the gap stated in the README.

## Open questions (PRD §23)

| ID | Question | Owner | Decide by | Checklist |
|---|---|---|---|---|
| Q1 | ~~Pay by `PAYPAL_ID` instead of `EMAIL`?~~ Decided (PRD v2.1): yes, confirm in SPIKE-10 | A | SPIKE-10 | P0-19 |
| Q2 | ~~`HIGH_RISK` approvable in demo?~~ Decided (PRD v2.1): only by 2 distinct eligible humans, not the uploader. A to confirm | A+B | Oct 27 | P1-18 |
| Q3 | Decided 2026-10-26: OpenAI `gpt-6.1-sol` for extraction + reasoning; $1.00 per invoice execution, ≤4 requests incl. retries. See TOOLS for rationale and implementation limits. | B closeout; A implementation | Decided | P0-16 |
| Q4 | Decided 2026-10-27: official AG Studio dark base and shared application tokens; native controls, keyboard focus and AA contrast retained. | B | Done | P1-17 |

Record each answer as an ECC team memory (`kind: decision`) and in the PRD.

## Top risks to watch (full register: PRD §22)
- Sandbox slow/unavailable while recording → rough cut Nov 5, replay mode labelled.
- AG Studio licence expiring during judging → SPIKE-7, ask AG Grid for a key to Dec 15.
- Scope overrun → weekly exit-criteria check, cut order above.
- Secrets leaked in a public repo → `.env` ignored, gitleaks in CI.
- Payee swap / insider / crash mid-payout (PRD v2.1 review) → R15–R20 never cut; crash and SoD tests in P4. Threat model: PRD §4.0.

## P0 closeout delegation — 2026-10-25

User authorized Dev B and task-specific subagents to finish all remaining P0 work today. Checklist ownership/claims are the task state. P0-15, P0-16, P0-19 and P0-20 are temporarily assigned to Dev B for closeout; Dev A retains the P1+ application responsibilities. Agents use isolated branches/worktrees and report verification evidence. Existing pull requests keep their review and merge gates.

## P1 completion delegation — 2026-10-27

The user authorized Dev B to finish every locally achievable P1 task before the account usage window ends, batching implementation and final validation while minimizing Git calls. Dev B temporarily owns the unclaimed P1-12/P1-21/P1-22/P1-23 settlement and exit work in addition to the remaining B items. Existing P1-11 work and review gates are preserved. Real AP/finance interviews, live sandbox evidence and hosted account setup are completed only when actual evidence exists; no research or deployment success is inferred from mocks. R1–R4 changes still require the other developer to review before merge.
