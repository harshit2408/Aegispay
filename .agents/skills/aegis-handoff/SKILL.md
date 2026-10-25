---
name: aegis-handoff
description: Finish an AegisPay Dev B session by verifying changes, updating checklist and shared decisions, appending a teammate handoff, committing and pushing. Use for handoff, wrap up or end of session in this repo.
---

Run in the main thread. Follow root `AGENTS.md` and PRD §6.1; do not delegate to Claude's memory-keeper.

1. Review the diff and run applicable checks from AGENTS.md. Failed checks block the implementation
   commit/push; record blockers accurately. Do not claim unrun checks passed.
2. Commit intended implementation paths using a conventional message citing checklist ID and PRD sections.
   Mark completed checklist items `[x]` with that short SHA; preserve unfinished WIP claims.
3. Search shared memory before saving a new decision/lesson. Use `ecc memory save --scope team
   --source-harness codex` when available (consult `ecc memory save --help`); otherwise append the
   decision and evidence to the session log, and report the memory CLI limitation. Never store secrets.
4. Append a dated Dev B entry at the bottom of `docs/SESSION_LOG.md` using its existing template.
   Include actual test results, commits, unfinished work, next dependencies, cross-owner edits,
   contract changes, blockers and anything Dev A must know. Commit checklist/log/memory updates.
5. Pull --rebase from the upstream without rewriting published commits, then push; on a new branch
   use `git push -u origin <branch>`. Rerun checks if sync changes code. Open a PR for code/config/scripts,
   preserving required review and CI; never force-push or auto-merge. Report push/PR result and blockers.
