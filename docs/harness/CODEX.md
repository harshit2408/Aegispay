# Codex harness — Dev B

Dev A uses Claude Code. Dev B uses Codex for full stack + data, frontend/AG Studio, CI/Render and video;
the authoritative ownership list is in [PLAN](../PLAN.md). Both read the same PRD, checklist,
session log, `.claude/rules/`, routing table and `.ecc/memory/team/`. Task state has no second home.

## Starting on this machine

Prerequisites: Git, Node >= 18, Python >= 3.12 and Codex. Open Codex in this repository.
Project config and hooks load only after the workspace/hook sources are trusted in the client;
this repository does not grant itself trust or change your global model, authentication or permissions.
Restart/open a new Codex session to discover the new project skills/configuration.

`.codex/config.toml` supplies `AEGIS_DEV=B` and `ECC_MEMORY_HARNESS=codex` to shell commands.
It configures the existing fetch MCP server using `python -m mcp_server_fetch`.
Install that dependency with `python -m pip install mcp-server-fetch` if missing.
Verify registration with `codex mcp list` and client connection status with `/mcp` in Codex CLI.
MCP availability also depends on the client surface; a configured entry alone is not a connection test.

Backend environment (PowerShell, repo root):

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e 'backend[dev]'
.venv/Scripts/python.exe -m ruff check backend
.venv/Scripts/python.exe -m pytest backend
node --test .codex/tests/context.test.mjs
```

The shared memory CLI is optional: `ecc memory search "ownership"` and `ecc memory doctor --scope team`.
If unavailable, read the committed Markdown directly; memory writes can be recorded in the handoff
until the CLI is available. Do not run Claude-specific global rule/plugin installers for Codex.

## Claude workflow mapped to Codex

| Claude | Codex |
|---|---|
| `CLAUDE.md` | Root `AGENTS.md`: context order, Dev B ownership, rules and verification |
| SessionStart brief | `.codex/hooks.json` → read-only `context.mjs brief`; fallback: run it manually |
| Prompt routing | Same routing JSON → `context.mjs route`; disciplines applied in the main thread |
| Automatic path rules | AGENTS.md explicitly directs reading the existing `.claude/rules/` files |
| Ponytail agents | Same persona files as domain guidance; no duplicated agent definitions |
| `/standup` | `$aegis-standup` in `.agents/skills/` |
| `/handoff` | `$aegis-handoff` in `.agents/skills/` |
| Post-test automatic Git sync | Read-only reminder; Codex verifies completion, then explicitly commits/syncs/pushes |
| ECC memory | Same portable team vault; new entries use `--source-harness codex` |
| Ponytail/caveman | Smallest complete change and concise chat in AGENTS.md; normal persisted prose |
| Obsidian | Same `docs/` vault and canvas; preserve existing Markdown/JSON formats |

Hooks use Git-root resolution so starting from a subdirectory works, including PowerShell on Windows.
They do not pull, push, inspect secrets, or conclude that an absent error means tests passed.
PostToolUse responses may represent a still-running command; wait for the final result.
If hooks are unavailable on a client, AGENTS.md and the two skills provide the explicit workflow.

Claude marketplace plugins (`ecc:*`, ponytail, caveman, obsidian) are not installed Codex tools.
Their automatic reviews, telemetry and learning hooks are not claimed here. Perform the relevant
review disciplines directly and report actual evidence. Money-safety changes still need Dev A's review.
PRD §6.1 governs branches/PRs; never auto-merge or force-push.

Official references used for this setup: [customization](https://learn.chatgpt.com/docs/customization/overview),
[hooks](https://learn.chatgpt.com/docs/hooks), [project configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
[shell environment](https://learn.chatgpt.com/docs/config-file/config-advanced#shell-environment-policy),
and [MCP](https://learn.chatgpt.com/docs/extend/mcp).
