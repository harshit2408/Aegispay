# AGENTS.md

Shared project instructions. Dev A uses Claude Code (`CLAUDE.md`); Dev B uses Codex.

1. Spec: `docs/PRD_SRS.md` is the single source of truth. Follow its structure, schemas and rules R1–R25 (§4; threat model §4.0).
2. State: `docs/CHECKLIST.md`. Claim an item with ` (WIP A|B)` before working; mark `[x]` + short SHA when done.
3. Plan and ownership: `docs/PLAN.md`. Dev A = PayPal/settlement/agents/auth; Dev B = full stack + data (models/migrations/seeder/audit/matching), frontend/AG Studio, CI/Render/video.
4. Before you stop: run the tests, commit (conventional message citing the checklist ID), `git pull --rebase`, `git push`,
   and append a handoff entry to `docs/SESSION_LOG.md`.
5. Money is integer cents. LLM code gets read-only tools. `[SPIKE-n]` behaviour is stubbed until PRD §18 records the result.
6. Write the smallest complete change (ponytail): reuse what exists, no unrequested abstractions — but never cut validation, error handling, security, accessibility or the test for money logic.
7. Never commit secrets.

## Codex — Dev B

Read `docs/CHECKLIST.md`, `docs/PLAN.md`, `docs/PRD_SRS.md`, then the latest entry at the bottom of
`docs/SESSION_LOG.md` before implementation. Search `.ecc/memory/team/` for relevant decisions and lessons;
the checklist alone holds task state. Default to Dev B; no need to ask the developer identity again.
See `docs/harness/CODEX.md` for setup and differences from Claude.

At session start run `node .codex/hooks/context.mjs brief` if the SessionStart hook did not provide a brief.
Use `$aegis-standup` for a full brief and `$aegis-handoff` before ending a session.
For each implementation request run `node .codex/hooks/context.mjs route "<task summary>"` if the prompt hook
did not supply routes. It reads the shared `.claude/routing.json`; treat agent names as review disciplines,
not installed Codex plugins. Work in the main thread unless delegation is explicitly requested.

Before editing, read the applicable shared rule files (Codex does not auto-load Claude path rules):

| Scope | Read |
|---|---|
| `backend/**`, compose, Render | `.claude/rules/backend.md` |
| `frontend/**` | `.claude/rules/frontend.md` |
| Money/payee/ledger/models/schema/webhooks and their tests | `.claude/rules/money-safety.md` |
| LLM/forensics/decision/tools/evals | `.claude/rules/ai-agents.md` and money-safety |

Use the corresponding `.claude/agents/ponytail-{frontend,backend,ai}.md` as domain guidance;
their Claude tools, slash commands and automatic sync instructions do not apply to Codex.
Reuse existing code, platform features and dependencies before adding code. Preserve validation,
error handling, security, accessibility and money tests. Keep chat concise; persisted prose stays normal.

Run from the repo root using the local virtual environment (PowerShell):

```powershell
.venv/Scripts/python.exe -m ruff check backend
.venv/Scripts/python.exe -m pytest backend
node --test .codex/tests/context.test.mjs
# Once frontend/package.json exists, run its applicable tests and npm run build from frontend/.
```

Git: claim + push first. Code/config/scripts go on `b/<ID>-<slug>` and through a PR into `main`
(PRD §6.1); docs/checklist-only updates may go directly to `main`. Stage only intended paths.
After tests pass, review the diff using the route's disciplines; record actual checks and limitations.
Commit with a conventional message citing the checklist ID and relevant PRD sections, pull --rebase,
then push. Sync main into a feature branch with `git fetch origin` + `git merge origin/main`;
never rebase published feature commits or force-push. If sync changes tested code, rerun checks.
Hooks only remind: they never infer test success or run Git mutations. Finish checklist items with
the implementation SHA, append a handoff, and save new cross-developer decisions to team memory.
Other-developer review remains mandatory for R1–R4 changes; do not auto-merge a PR.

Do not read real `.env`/`.env.*` secret files or credential stores; `.env.example` is a safe template.
Never commit credentials or the AG licence key. LLM application tools stay read-only (PRD R2).
