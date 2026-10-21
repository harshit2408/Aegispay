---
tags: [harness, caveman]
---
# Caveman in AegisPay

[Caveman](https://github.com/JuliusBrussee/caveman) (v4.0) is a Claude Code plugin that makes Claude's **chat replies**
terse: answer first, no ceremony, every technical fact kept. The project claims about 65% fewer output tokens
(its own benchmark, output tokens only). Installed at **project scope**; on by default for both developers.

## What changes and what does not

| Caveman applies to | Caveman never applies to |
|---|---|
| Chat replies in the terminal / IDE panel | Code, code comments, commit messages, PR text |
| Status lines between tool calls | `docs/*`, the checklist, the session log, ADRs |
| | ECC team memories, project skills, anything persisted |
| | Security warnings, irreversible-action confirmations, questions to you |

So the repo stays readable for judges and for the other developer; only the conversation gets shorter.
The `memory-keeper` agent is explicitly told to write in plain prose.

Example — normal: *"Sure! I took a look, and it seems the issue is likely caused by the token expiry check…"*
Caveman: *"Bug in auth middleware. Expiry check uses `<` not `<=`. Fix:"*

## Commands

| Command | Effect |
|---|---|
| `/caveman` | on (default level) |
| `/caveman ultra` | maximum compression — fine for long refactor sessions |
| `/caveman lite` | lighter touch — use when pairing or explaining to someone new |
| `/caveman status` | show current mode |
| "stop caveman" / "normal mode" | off for this session |
| `/caveman-commit` | terse conventional commit message for staged changes |
| `/caveman-review` | one-line-per-finding code review |
| `/caveman-compress <file>` | compress a memory/notes file to save input tokens (keeps a `.original.md` backup). **Do not** run it on `CLAUDE.md`, `docs/CHECKLIST.md` or `docs/SESSION_LOG.md` — hooks parse those |
| `/caveman-stats` | tokens saved so far |

## Configuration
- Default mode comes from `CAVEMAN_DEFAULT_MODE` in `.claude/settings.json` (`caveman`).
  Values: `caveman`, `ultracave`, `megacave`, `manual` (installed but starts off), `off`.
- Personal override without touching the team file — `.claude/settings.local.json`:
  `{ "env": { "CAVEMAN_DEFAULT_MODE": "manual" } }`.
- Works alongside ponytail: ponytail decides **what code** to write, caveman decides **how the reply reads**.
- Disable entirely for one machine: `{"enabledPlugins": {"caveman@caveman": false}}` in `.claude/settings.local.json`.
