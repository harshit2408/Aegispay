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

The SessionStart adapter pulls a clean upstream and loads the brief. If hooks are unavailable,
run `node .codex/hooks/context.mjs brief` and sync explicitly before claiming work.
Use `$standup` for a full brief and `$handoff` before ending a session.
The previous `$aegis-standup` / `$aegis-handoff` names remain aliases.
For each implementation request run `node .codex/hooks/context.mjs route "<task summary>"` if the prompt hook
did not supply routes. It reads the exact `.codex/routing.json` mirror. Delegate matching work to the
native project agent in `.codex/agents/`: `ponytail-backend`, `ponytail-frontend`, `ponytail-ai`,
or `memory-keeper`. This project instruction requests the same delegation as the Claude setup.
If this client cannot select custom agents directly, pass the complete TOML `developer_instructions`
to a worker. External `ecc:*` routes still require installed tools; otherwise apply their review disciplines.

The PreToolUse adapter injects applicable mirrored path rules. Read them manually if hooks are unavailable:

| Scope | Read |
|---|---|
| `backend/**`, compose, Render | `.codex/rules/backend.md` |
| `frontend/**` | `.codex/rules/frontend.md` |
| Money/payee/ledger/models/schema/webhooks and their tests | `.codex/rules/money-safety.md` |
| LLM/forensics/decision/tools/evals | `.codex/rules/ai-agents.md` and money-safety |

Every project `.claude/` file has an exact same-relative-path copy under `.codex/`, including settings,
rules, routing, hooks, Markdown agents and skills. Local settings are copied to an ignored local file.
The native TOML agents additionally preserve full agent bodies with a Codex host mapping appended.
The native `$standup`/`$handoff` skills preserve `.claude/skills/` and delegate to `memory-keeper`.
When a PR changes Claude sources, regenerate and commit Codex mirrors in the same branch. Repeat
after rebasing or incorporating another source-changing branch. CI runs `--check` to prevent
incomplete or stale ports and prints the regeneration command. Do not replace definitions with summaries.
Reuse existing code, platform features and dependencies before adding code. Preserve validation,
error handling, security, accessibility and money tests. Keep chat concise; persisted prose stays normal.

Run from the repo root using the local virtual environment (PowerShell):

```powershell
.venv/Scripts/python.exe -m ruff check backend
.venv/Scripts/python.exe -m pytest backend
node --test .codex/tests/*.test.mjs
.venv/Scripts/python.exe .codex/scripts/sync-claude-context.py --check
# Once frontend/package.json exists, run its applicable tests and npm run build from frontend/.
```

Git: claim + push first. Code/config/scripts go on `b/<ID>-<slug>` and through a PR into `main`
(PRD §6.1); docs/checklist-only updates may go directly to `main`. Stage only intended paths.
After tests pass, review the diff using the route's disciplines; record actual checks and limitations.
Commit with a conventional message citing the checklist ID and relevant PRD sections, pull --rebase,
then push. Sync main into a feature branch with `git fetch origin` + `git merge origin/main`;
never rebase published feature commits or force-push. If sync changes tested code, rerun checks.
The post-test adapter syncs only a clean tree after an isolated test runner returns an explicit final
zero exit code and no failure summary. Failed/running/unknown/compound-command results never sync.
Pulled changes require a new test run before auto-push. `AEGIS_AUTOPUSH=0` disables auto-push.
Managed permissions still apply; neither settings copies nor hooks grant themselves full access.
Finish checklist items with
the implementation SHA, append a handoff, and save new cross-developer decisions to team memory.
Other-developer review remains mandatory for R1–R4 changes; do not auto-merge a PR.

Do not read real `.env`/`.env.*` secret files or credential stores; `.env.example` is a safe template.
Never commit credentials or the AG licence key. LLM application tools stay read-only (PRD R2).
