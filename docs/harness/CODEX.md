# Codex harness — Dev B

Dev A uses Claude Code. Dev B uses Codex for full stack + data, frontend/AG Studio, CI/Render and video;
the authoritative ownership list is in [PLAN](../PLAN.md). Both read the same PRD, checklist,
session log and `.ecc/memory/team/`. Every shared `.claude/` file has a same-relative-path mirror
under `.codex/`; task state has no second home.

## Complete source copy

All 16 shared files are copied: four Markdown agents, two skills, four rules, four hook scripts,
`routing.json` and `settings.json`. The machine-local `settings.local.json` is also copied locally
and Git-ignored. No local settings values appear in the inventory, logs, or commits.
`.codex/source-inventory.json` inventories the shared files with line-ending-normalized SHA-256
checksums for Git portability; the generator checks every local mirror byte-for-byte.

The original-format copies are retained intact. Native `.codex/agents/*.toml`, discoverable
`.agents/skills/`, `.codex/config.toml`, `.codex/hooks.json`, and the generated
`.codex/rules/claude-permissions.rules` activate the equivalent Codex mechanisms. Merely copying
Claude's `settings.json` or putting skills under `.codex/skills/` would not activate them in Codex.

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
node --test .codex/tests/*.test.mjs
```

The shared memory CLI is optional: `ecc memory search "ownership"` and `ecc memory doctor --scope team`.
If unavailable, read the committed Markdown directly; memory writes can be recorded in the handoff
until the CLI is available. Do not run Claude-specific global rule/plugin installers for Codex.

## Claude workflow mapped to Codex

| Claude | Codex |
|---|---|
| `CLAUDE.md` | Root `AGENTS.md`: context order, Dev B ownership, rules and verification |
| SessionStart pull + brief | `lifecycle.mjs session`: clean upstream pull and progress/handoff brief |
| Prompt routing | Exact `.codex/routing.json` mirror → `context.mjs route`; native agent delegation |
| Automatic path rules | PreToolUse → `lifecycle.mjs rules`: matching mirrored rule bodies |
| Four `.claude/agents/*.md` definitions | Full bodies in native `.codex/agents/*.toml`: backend, frontend, AI and memory-keeper |
| `/standup` | Full original skill at `.agents/skills/standup/SKILL.md`; invoke `$standup` |
| `/handoff` | Full original skill at `.agents/skills/handoff/SKILL.md`; invoke `$handoff` |
| Post-test automatic Git sync | PostToolUse → `lifecycle.mjs test`: explicit successful final result, clean-tree pull/push |
| ECC memory | Same portable team vault; new entries use `--source-harness codex` |
| Enabled plugins | Native Codex installations: ECC 2.2.3, Ponytail 5.3.2, Caveman 4.0.1, Obsidian 1.0.1 |
| Command allow rules | Generated native prefix rules from Claude's seven `permissions.allow` entries |
| Secret-file deny rules | PreToolUse rejects direct real `.env` access; `.env.example` stays usable; managed permissions remain authoritative |
| Obsidian | Installed for both harnesses; `docs/` registered as an Obsidian vault with live CLI verification |

Hooks use Git-root resolution so starting from a subdirectory works, including PowerShell on Windows.
Session-start sync only pulls a clean upstream. The post-test adapter recognizes isolated test
runner invocations and requires a structured final zero exit code with no failure summary.
Plain stdout (even JSON-looking stdout), asynchronous session IDs, missing exit codes, compound
commands, collection/listing commands and failures cannot trigger sync. A dirty tree, merge/rebase
in progress or missing upstream defers sync. Pulled changes require a test rerun before pushing.
Set `AEGIS_AUTOPUSH=0` in the ignored local settings environment for pull-only behavior.
Clients that provide stdout-only test responses require explicit manual sync after verification.
No hook grants itself permissions or disables the session's managed sandbox/approval policy.
If hooks are unavailable on a client, AGENTS.md and the two skills provide the explicit workflow.

The four repo agents and two repo skills are complete ports, not persona summaries. Their source
instructions remain unchanged; an appended host mapping handles Codex tool names, native skill paths,
Dev B defaults, memory attribution and lifecycle transport. The old `$aegis-*` names are aliases.
The Claude-only `model: sonnet`, `tools` and `color` frontmatter cannot be copied into the Codex schema:
all agents inherit the parent model/tools/permissions; color is omitted. No model is substituted.

After editing any `.claude/` file, run:

```powershell
.venv/Scripts/python.exe .codex/scripts/sync-claude-context.py
.venv/Scripts/python.exe .codex/scripts/sync-claude-context.py --check
```

The generator uses PyYAML (included with the existing backend dev environment), copies every shared
source byte-for-byte, preserves every original instruction in the native ports, and validates TOML.
It rejects source/output symlinks and junctions or escaped output paths. CI catches changed or
orphaned mirrors and native definitions. It never logs or commits machine-local settings values.
Routing now delegates to the actual project agents. Standup and handoff delegate to `memory-keeper`.
Clients without custom-role selection can pass the complete definition to a worker.

The separate Ponytail/Caveman/Obsidian plugins were installed from the machine's existing Claude caches with
`codex plugin marketplace add <versioned-cache-root>` then `codex plugin add <name>@<name>`.
All 33 Ponytail and 89 Caveman skill/command/hook files compared byte-for-byte equal to their installed
Claude versions. This is machine-local installation, not vendored third-party source in the repo.
Native plugin hook trust and UI activation still require the client's supported hook review/new-session
flow; they were not exercised through the UI. The Caveman plugin's Bash launcher and Claude-specific
usage reader are upstream host limitations; no Codex token savings are claimed.

ECC was installed from the marketplace declared in `.claude/settings.json` using
`codex plugin marketplace add https://github.com/affaan-m/ECC.git` and `codex plugin add ecc@ecc`.
`codex plugin list --json` verifies all four enabled plugin identities; their actual native tools/skills
depend on client discovery on the next session. Third-party host-specific telemetry/statusline hooks
are not represented as tested UI features. Obsidian's Claude plugin and live vault CLI are independently
verified; details and installed helper versions are in [OBSIDIAN](OBSIDIAN.md).
Money-safety changes still need Dev A's review.
PRD §6.1 governs branches/PRs; never auto-merge or force-push.

Official references used for this setup: [customization](https://learn.chatgpt.com/docs/customization/overview),
[hooks](https://learn.chatgpt.com/docs/hooks), [project configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
[shell environment](https://learn.chatgpt.com/docs/config-file/config-advanced#shell-environment-policy),
and [MCP](https://learn.chatgpt.com/docs/extend/mcp).
Native custom-agent format: [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).
