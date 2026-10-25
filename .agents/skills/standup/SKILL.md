---
name: standup
description: AegisPay start-of-session brief. Pulls the teammate's work and reports phase progress, what the other developer did, your next checklist items, blockers and relevant team memories. Use at the start of every session, or when asked "where are we", "what's next", "status".
---
# /standup

Delegate to the `memory-keeper` agent with: "Run the /standup procedure for Dev $AEGIS_DEV."

If `AEGIS_DEV` is unset, ask the user whether they are Dev A (backend, PayPal, agents) or Dev B
(frontend, AG Studio, video) and tell them to add it to `.claude/settings.local.json`:

```json
{ "env": { "AEGIS_DEV": "A" } }
```

Present the brief in this shape (keep it under 20 lines):

```
Phase P1 (Oct 23–27) · exit: headless S1 · 4 days left · checklist 12/97
Other dev since your last session: P1-13, P1-14 done; P1-15 WIP
Your next: P1-03 …, P1-04 …, P1-05 …
Blocked: none
Team memory to know: SPIKE-2 = no (reads via VendorGateway)
Suggested first move: claim P1-03 → route money-paypal → agent ponytail-backend
```

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
- Codex lifecycle adapters run the copied session and post-test sync workflow. Only confirmed
  successful final test results may sync. Unknown/running/failed results never sync. Preserve
  PRD 6.1 branches/PRs, never force-push, rerun checks if sync changes code. If hooks are
  unavailable, explicitly perform the same commit/pull/push sequence after verified checks.
- ecc:* agents/skills are not provided by these four project files. Use them only if actually
  installed; otherwise perform their named review discipline and disclose that limitation.
- The original memory-keeper's model: sonnet has no Codex equivalent configured here.
  All four ports inherit the current Codex model, tools and permission policy. Claude UI
  color metadata has no Codex equivalent. No provider/model or permissions are guessed.
