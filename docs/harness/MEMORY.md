---
tags: [harness, memory]
---
# Memory in AegisPay

Two developers, two Claude sessions, many subagents. Each kind of knowledge has exactly one home:

| Knowledge | Home | Shared? | Who writes it |
|---|---|---|---|
| Task state: done / WIP / blocked | `docs/CHECKLIST.md` | yes (git) | whoever does the work |
| What happened this session, what's next | `docs/SESSION_LOG.md` | yes (git) | `/handoff` |
| Decisions, spike results, facts, lessons, gotchas, runbooks | **ECC team vault** `.ecc/memory/team/` | yes (git) | `memory-keeper`, or `ecc memory save` |
| Architecture trade-offs | `docs/adr/` | yes | dev + `ecc:architect` |
| Repeated procedures | `.claude/skills/<name>/SKILL.md` | yes | `memory-keeper` spawns them |
| Scratch / machine-local context | ECC project vault `.ecc/memory/project/` | no (git-ignored by ECC) | either |
| One dev's personal preferences | Claude auto-memory (`~/.claude/projects/…/memory/`) | no | Claude |
| ECC session snapshots / instincts | `~/.claude/session-data`, ECC homunculus | no | ECC hooks |

## ECC Memory Vault (the shared brain)
Initialised with `ecc memory init --scope team` and `--scope project`. Documents are portable `ecc.memory.v1`
Markdown, readable by Claude, Codex, Cursor and others. Writes are create-only and reject known credential shapes.

```bash
ecc memory search "spike"                       # recall (project + team scopes)
ecc memory search --kind decision --limit 10
ecc memory read <memory-id>
ecc memory save --scope team --source-harness claude --kind lesson \
  --title "Sandbox payouts take ~40s to reach SUCCESS" --tag paypal --tag gotcha --stdin <<'EOF'
Measured 2026-10-22: batch PENDING→SUCCESS in 31–48 s (n=5). Webhook arrived 2–6 s after. See P0-09.
EOF
ecc memory handoff --from claude --target codex --title "…" --stdin   # cross-harness handoff
ecc memory doctor --scope team                  # validate the vault
```
Kinds: `context, decision, fact, handoff, lesson, note, preference, runbook`. Commit `.ecc/memory/team/` with your work.
Optional MCP access: the `ecc-memory-mcp` binary (from `npm i -g ecc-universal`) can be added with
`claude mcp add ecc-memory --scope local -- ecc-memory-mcp` so agents get memory tools instead of shelling out.

## The memory-keeper agent
`.claude/agents/memory-keeper.md` ("Archivist") is the only agent that curates memory. It:
1. runs `/standup` (pull, progress, other dev's work, relevant memories) and `/handoff` (tick checklist, save memories, log, push);
2. searches before saving so nothing is stored twice; links superseding entries with `--link`;
3. **spawns skills**: when the log or memories show the same procedure done twice, it writes
   `.claude/skills/<name>/SKILL.md` (and optionally a route in `.claude/routing.json`), so the next agent follows the recipe instead of rediscovering it.

## ECC session tools (per machine, complementary)
- `/ecc:save-session` / `/ecc:resume-session` — snapshot and restore a long working session.
- `/ecc:learn`, `/ecc:instinct-status`, `/ecc:evolve` — ECC continuous learning (instincts → skills), personal scope.
These are personal; anything the other developer needs must also go to the team vault or the session log.
