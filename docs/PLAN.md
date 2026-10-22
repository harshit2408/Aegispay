---
tags: [aegispay, plan]
source: "[[PRD_SRS]] §19"
---
# AegisPay — Delivery Plan

Spec: [[PRD_SRS]] (single source of truth). State: [[CHECKLIST]]. Daily log: [[SESSION_LOG]]. Map: `maps/AegisPay.canvas`.

**Deadline:** 2026-11-12 12:00 PT (01:30 IST Nov 13). **We submit Nov 11.** Judging Dec 1–15 — demo and licences must live until Dec 15.

## Who owns what

| | Dev A | Dev B |
|---|---|---|
| Area | PayPal (both apps), settlement + spend ledger + reconciler, webhooks, LangGraph + AI agents, forensics, policy gate, auth, evals | Full stack + data: backend skeleton, DB schema + migrations, seeder, audit chain, match + duplicates (R9), docker-compose, Render deploy; frontend, AG Studio cockpit + widgets, vendor portal, CI, user research, video |
| Main thread agent | `claude --agent ponytail-backend` or `ponytail-ai` | `claude --agent ponytail-frontend` or `ponytail-backend` (data items) |
| Owns dirs | `backend/app/{paypal,settlement,decision,forensics,agents,routers}/`, `docs/adr/`, `docs/EVAL.md`, `docs/TOOLS.md`, `docs/SANDBOX_SETUP.md` | `frontend/`, `backend/app/{models,db.py,config.py,audit,demo}/`, `backend/migrations/`, `docker-compose.yml`, `.github/`, `render.yaml`, `docs/USER_RESEARCH.md` |
| Shared | `docs/CHECKLIST.md`, `docs/SESSION_LOG.md`, `docs/PRD_SRS.md`, `CLAUDE.md`, `.claude/` | same |

Touching the other developer's directory: allowed for small fixes, but say so in the commit message and the session log.

## Phases (detail and IDs in [[CHECKLIST]])

| Phase | Dates | Exit criterion | Critical items |
|---|---|---|---|
| P0 Setup + spikes | Oct 21–22 | Spike table filled; go/no-go per assumption | SPIKE-1…9, LICENSE, CI, Render |
| P1 Foundations | Oct 23–27 | **Headless S1**: CLI upload → sandbox payout → invoice `PAID` | PayPal clients, SettlementService, reconciler, webhooks; app shell |
| P2 Decision engine | Oct 28 – Nov 2 | **Headless S1–S4 pass**; first EVAL numbers | forensics, match, risk, gate, graph, property tests; ledger/gauge/exposure widgets |
| P3 Agents + deploy | Nov 3–6 | **Hosted S1–S4 green on Render**; rough cut Nov 5 | Investigator, auth, deploy by Nov 4; decision-path + settlement widgets |
| P4 Hardening | Nov 7–8 | Feature freeze Nov 8 EOD | chaos + contract tests, Playwright |
| P5 Ship | Nov 9–11 | Submitted | README, TOOLS, EVAL, video, clean-clone check |

## The interface contract between A and B

Dev B must never wait on Dev A, so the seam is fixed early and mocked:

1. **OpenAPI** — PRD §13 routes. Dev A publishes `backend/openapi.json` by end of Oct 24 (stubs are fine). Dev B generates `frontend/src/lib/api.ts` from it and builds against a mock until P2.
2. **SSE event schema** — `{type: "node"|"settlement"|"webhook", execution_id, name, status, ts, payload}`. Agree by Oct 27; any change is a commit touching both sides plus a team memory entry.
3. **Execution status enum** — exactly PRD §8 `invoice_executions.status`. No new statuses without updating PRD §8.

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

**Never cut:** payee lock, async settlement, duplicate check, operator auth, LICENSE + README + judge guide, the video.

## Open questions (PRD §23)

| ID | Question | Owner | Decide by | Checklist |
|---|---|---|---|---|
| Q1 | Pay by `PAYPAL_ID` instead of `EMAIL`? | A | after SPIKE-3 | P0-09 |
| Q2 | `HIGH_RISK` approvable in demo, or reject-only? | A+B | Oct 27 | P1-18 |
| Q3 | Which LLM provider + per-run cost cap? | A | Oct 22 | P0-16 |
| Q4 | Terminal aesthetic vs AG Studio theme base? | B | Oct 23 | P1-17 |

Record each answer as an ECC team memory (`kind: decision`) and in the PRD.

## Top risks to watch (full register: PRD §22)
- Sandbox slow/unavailable while recording → rough cut Nov 5, replay mode labelled.
- AG Studio licence expiring during judging → SPIKE-7, ask AG Grid for a key to Dec 15.
- Scope overrun → weekly exit-criteria check, cut order above.
- Secrets leaked in a public repo → `.env` ignored, gitleaks in CI.
