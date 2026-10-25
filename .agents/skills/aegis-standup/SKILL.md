---
name: aegis-standup
description: Start or resume an AegisPay Dev B session with checklist progress, teammate handoff, shared memories and next dependencies. Use for standup, status or what's next in this repo.
---

Run in the main thread; Dev B uses Codex and Dev A uses Claude.

1. Read root `AGENTS.md`. Inspect `git status --short --branch` and the current upstream.
   If clean and no merge/rebase is underway, run `git pull --rebase` on the upstream;
   for a published feature branch use `git fetch origin` + `git merge origin/main` to include main.
   Preserve dirty work and report conflicts without resetting or discarding changes.
2. Run `node .codex/hooks/context.mjs brief`. Read `docs/PLAN.md`, relevant PRD sections and
   the latest handoff in `docs/SESSION_LOG.md`. Search `.ecc/memory/team/` (or `ecc memory search`)
   for relevant decisions. Memory may be stale; PRD and checklist win.
3. Report phase/exit criterion, progress, teammate changes, Dev B's WIP, next dependencies,
   blockers and the suggested first claim in fewer than 20 lines. Respect other claims.
   Do not begin an unrelated implementation merely because it is the next checklist item.
