# AegisPay — agent context

Zero-trust agentic payables on PayPal: an AI agent pays vendor invoices through PayPal Payouts, but by
architecture it can only pay **verified vendors**, only for **goods received**, only within a **signed mandate**.
Hackathon build, 2 developers, **submit 2026-11-11**.

## Read first (in this order)
1. `docs/CHECKLIST.md` — what is done, who is on what, what is next. **State lives here.**
2. `docs/PLAN.md` — phases, owners, the A↔B interface contract, sync rhythm, cut order.
3. `docs/PRD_SRS.md` — the spec and single source of truth. Cite its sections (§n) in commits and replies.
4. Last entry of `docs/SESSION_LOG.md` — the other developer's latest handoff.

The `SessionStart` hook pulls the remote and injects a brief of 1, 2 and 4. `/standup` gives the long version.

## Who is who
- `AEGIS_DEV=A` — backend, PayPal (both sandbox apps), LangGraph + AI components, settlement, evals.
- `AEGIS_DEV=B` — frontend, AG Studio cockpit + widgets, vendor portal, CI/Render, user research, video.
- Set it once per machine in `.claude/settings.local.json` → `{"env": {"AEGIS_DEV": "A"}}`.

## Non-negotiables (PRD §4 — tests enforce them)
- **R1** Payee only from `vendor_master`. `SettlementService.execute(execution_id)` is the only way money moves.
- **R2** LLM-reachable code gets read-only tools. `paypal/payouts.py` imported only from `settlement/`.
- **R3** Amount = `min(billed, accepted_qty × PO unit price)`; humans may lower, never raise.
- **R4** Integer cents. `float` near money is a build failure.
- **R5–R11** idempotency keys from `execution_id`, async payouts, atomic spend cap, duplicate indexes, raw-byte webhook verification, session-derived actor.
- **[SPIKE-n]** = unverified PayPal behaviour. Stub + test until PRD §18 records the result. Never fill it in from memory.
- Never commit `.env`, keys, sandbox credentials or the AG licence key.

## Team protocol (both humans and agents follow it)
1. **Claim** a checklist item before starting: append ` (WIP A|B)` to its line, commit, push.
2. **Work** test-first. When a test command passes, the `post-test-sync` hook pulls (rebase) and pushes your commits; if the tree is dirty it tells you to commit — do it, with a conventional message citing the checklist ID (`feat(settlement): P1-07 outbox worker`).
3. **Finish**: `[x]` + short SHA on the checklist line.
4. **End of session**: `/handoff` (memory-keeper writes the log entry, team memories, and pushes).
5. Contract changes (OpenAPI, SSE event shape, status enum) → tell the other dev in the session log and save a team memory `decision`.

## Routing — decided by a table, not by guesswork
- `.claude/routing.json` maps prompt keywords → **agent** to implement, **review** agents, **skills**, **rules**.
  The `UserPromptSubmit` hook prints the match (`route → …`) to the human and injects it into context. **Follow it** unless the user overrides.
- An explicit `/command` or `@agent-name` in the prompt skips routing.
- **Rules** in `.claude/rules/` load automatically by file path (money-safety, backend, frontend, ai-agents) — no choice involved.
- To change who handles what, edit `routing.json`. Full map: `docs/harness/HARNESS.md`.

| Work | Agent (implement) | Review before push |
|---|---|---|
| PayPal, settlement, ledger, webhooks, DB, API | `ponytail-backend` | `ecc:security-reviewer`, `ecc:python-reviewer` / `ecc:database-reviewer` |
| LangGraph, A1–A7, forensics, evals | `ponytail-ai` | `ecc:security-reviewer`, `ecc:python-reviewer` |
| Next.js, AG Studio widgets, pages | `ponytail-frontend` | `ecc:react-reviewer`, `ecc:typescript-reviewer` |
| E2E S1–S4 | `ecc:e2e-runner` | — |
| Broken build | `ecc:build-error-resolver` / `ecc:react-build-resolver` | — |
| Plans, ADRs, trade-offs | `ecc:planner`, `ecc:architect` | — |
| Standup, handoff, memory, new skills | `memory-keeper` | — |

## Installed harness (project scope, `.claude/settings.json`)
- **ECC** (`ecc@ecc`) — agents, skills, rules, hooks. Namespaced as `ecc:<name>`.
- **ponytail** — "lazy senior dev": smallest complete change, never cuts validation/security/a11y/tests. Default `full`. `/ponytail-review` on diffs.
- **caveman** — terse chat replies to save output tokens. Applies to chat only: code, comments, commits, docs and memories stay in normal prose. `/caveman lite|ultra`, "normal mode" to stop.
- **obsidian** — `docs/` is an Obsidian vault; skills `obsidian:json-canvas` (mind maps) and `obsidian:obsidian-markdown`.
- **Memory**: shared = ECC team vault `.ecc/memory/team/` (committed; `ecc memory search|save --scope team`). Personal = Claude auto-memory. Task state = the checklist, never memory.

Docs per tool: `docs/harness/` (HARNESS, PONYTAIL, CAVEMAN, OBSIDIAN, MEMORY).

## Repo layout
Target structure is PRD §6 (`backend/`, `frontend/`, `docs/`, `render.yaml`, `docker-compose.yml`). Create directories as checklist items need them — not ahead of time.
