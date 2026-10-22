# AGENTS.md

For any coding agent (Cursor, Codex, Copilot, Windsurf, Gemini…). Claude Code reads `CLAUDE.md`, which is the full version.

1. Spec: `docs/PRD_SRS.md` is the single source of truth. Follow its structure, schemas and rules R1–R25 (§4; threat model §4.0).
2. State: `docs/CHECKLIST.md`. Claim an item with ` (WIP A|B)` before working; mark `[x]` + short SHA when done.
3. Plan and ownership: `docs/PLAN.md`. Dev A = backend/PayPal/agents, Dev B = frontend/AG Studio/video.
4. Before you stop: run the tests, commit (conventional message citing the checklist ID), `git pull --rebase`, `git push`,
   and append a handoff entry to `docs/SESSION_LOG.md`.
5. Money is integer cents. LLM code gets read-only tools. `[SPIKE-n]` behaviour is stubbed until PRD §18 records the result.
6. Write the smallest complete change (ponytail): reuse what exists, no unrequested abstractions — but never cut validation, error handling, security, accessibility or the test for money logic.
7. Never commit secrets.
