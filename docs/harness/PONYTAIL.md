---
tags: [harness, ponytail]
---
# Ponytail in AegisPay

[Ponytail](https://github.com/DietrichGebert/ponytail) (MIT, v5.3) is a Claude Code plugin that makes the agent
behave like "the laziest senior dev in the room": the **smallest change that fully solves the task**, with a
reply a busy human reads once. It is installed at **project scope**, so both developers get it.

## What it does in practice
Before writing code it climbs a ladder and stops at the first rung that holds:
1. does this need to exist → 2. already in the codebase → 3. stdlib / platform feature → 4. installed dependency →
5. one readable line → 6. only then the minimum new code, **plus one small test** if it has logic.

It **never cuts** validation at trust boundaries, error handling that prevents data loss, security, accessibility,
or anything you asked for. Every reply ends with one or two lines: what was skipped or not checked, and any risk.
Deliberate shortcuts are marked `shortcut: <limit>, <when to upgrade>` so they can be collected later.

Why it fits this project: a 3-week hackathon where scope overrun is the top risk (PRD §22), and judges read the
code. Less code = less to break in the video, and the money tests still get written.

## How it is wired here

| Layer | What | Where |
|---|---|---|
| Base mode | `full` for every session and every subagent (plugin SessionStart + SubagentStart hooks) | `PONYTAIL_DEFAULT_MODE=full` in `.claude/settings.json` |
| Backend persona **"Ledger"** | payments veteran; integer cents, idempotency, Postgres constraints, R1–R11 | `.claude/agents/ponytail-backend.md` |
| Frontend persona **"Pixel"** | native elements + AG Studio built-ins first, never cuts a11y, UI never decides money | `.claude/agents/ponytail-frontend.md` |
| AI persona **"Sentinel"** | deterministic > one tool-less LLM call > read-only agent; evals or it didn't happen | `.claude/agents/ponytail-ai.md` |
| Routing | the routing table sends money/API/DB work to Ledger, UI to Pixel, LLM work to Sentinel | `.claude/routing.json` |
| Review | `/ponytail-review` is part of the `review-commit` route | — |

Each persona = ponytail's ladder + a domain "never cut" list taken from PRD §4 + a reply format.

## How to use it

```bash
claude --agent ponytail-backend      # Dev A: whole session as Ledger
claude --agent ponytail-ai           # Dev A: LangGraph / evals day
claude --agent ponytail-frontend     # Dev B: whole session as Pixel (or ponytail-backend for backend work)
```
Inside any session:

| Command | Use |
|---|---|
| `@agent-ponytail-backend <task>` | delegate one task to a persona |
| `/ponytail lite` · `/ponytail full` · `/ponytail ultra` · `/ponytail off` | intensity. `lite` names the smaller option and lets you pick; `ultra` also pushes back on the request itself — good for scope fights near feature freeze |
| `/ponytail-review` | review the diff like the dev who gets paged: bugs, security, load, missing tests, what to cut. Run before every push of money code |
| `/ponytail-audit` | whole-repo ranked audit — run at the end of P2 and P4 |
| `/ponytail-debt` | collect all `shortcut:` comments into a ledger — run before feature freeze (Nov 8) |
| `/ponytail band` | toggle the cost/token band above the prompt (terminal/desktop app only, not the VS Code panel) |

## Configuration
- Mode default: `PONYTAIL_DEFAULT_MODE` (project env) or `~/.config/ponytail/config.json`.
- Task board: on by default; writes per-task cost/tokens to `~/.config/ponytail/band/<project>.csv` on **your machine only**.
- Webhook: **off** (empty). Don't set one; it would send task rows off-machine. `/plugin configure ponytail@ponytail` to review.
- Change a persona: edit its file in `.claude/agents/`, commit, and the other dev gets it on the next pull.
