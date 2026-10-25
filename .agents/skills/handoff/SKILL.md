---
name: handoff
description: AegisPay end-of-session handoff. Ticks finished checklist items, saves decisions/spike results/gotchas to the ECC team memory vault, spawns a project skill if a procedure repeated, appends docs/SESSION_LOG.md, commits and pushes. Use when the user says handoff, wrap up, end of day/session, or before switching tasks for a long time.
---
# /handoff

Delegate to the `memory-keeper` agent with: "Run the /handoff procedure for Dev $AEGIS_DEV.
Session summary: <2–5 bullets of what we did and decided in this conversation>."

Pass the summary yourself — the agent cannot see this conversation. Include:
- checklist IDs touched and their state,
- every decision or spike result (with the evidence: status codes, timings, error text),
- gotchas that cost time,
- anything the other developer must do or know first.

After the agent returns, show the user the session-log entry it wrote and the push result.
If tests have not been run since the last code change, run them first (the post-test hook syncs git).

## Codex host mapping

The original project instructions above are preserved in full. For Codex-specific operations:
- Dev B uses Codex; default AEGIS_DEV=B. Dev A keeps Claude. Follow root AGENTS.md and PRD.
- The four project agents are native .codex/agents/*.toml definitions. Delegate to the named
  agent using the available agent tool. If custom roles are unavailable in this client, pass
  that definition's complete developer_instructions to a worker; do not silently omit the role.
- /standup and /handoff mean $standup and $handoff. The canonical Codex skills live in
  .agents/skills/. Create/update repeated-procedure skills there; then regenerate the Claude
  ports with .venv/Scripts/python.exe .codex/scripts/sync-claude-context.py after source edits.
- .claude/settings.local.json is Claude-only. Codex Dev B defaults live in .codex/config.toml.
- Use --source-harness codex for ECC writes. Personal scratch belongs to Codex-local context,
  not Claude auto-memory. Shared knowledge still lives in the existing ECC team vault.
- Read/Edit/Bash/Grep/Glob mean Codex file, shell and search tools, under inherited permissions.
  Use shell-appropriate syntax (PowerShell on Windows; body files instead of Bash heredocs).
- Claude post-test hooks do not run in Codex. Inspect final exit codes, then explicitly run
  the required commit/pull/push sequence. Preserve PRD 6.1 branches/PRs, never force-push,
  and rerun checks if sync changes code. An unavailable upstream is a reported blocker.
- ecc:* agents/skills are not provided by these four project files. Use them only if actually
  installed; otherwise perform their named review discipline and disclose that limitation.
- The original memory-keeper's model: sonnet has no Codex equivalent configured here.
  All four ports inherit the current Codex model, tools and permission policy. Claude UI
  color metadata has no Codex equivalent. No provider/model or permissions are guessed.
