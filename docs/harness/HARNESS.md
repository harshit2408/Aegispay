---
tags: [harness]
---
# AegisPay Claude harness — how it all fits

Everything here is committed, so both developers (and their agents) get the same setup on `git pull`.
Companion docs: [[PONYTAIL]] · [[CAVEMAN]] · [[OBSIDIAN]] · [[MEMORY]].

## 1. One-time setup per machine

```bash
# prerequisites: git, Node >= 18, Claude Code >= 2.1.290
git clone https://github.com/harshit2408/Aegispay.git && cd Aegispay

# ECC: plugin + memory CLI + user-level language rules (python, typescript)
npm install -g ecc-universal          # gives `ecc` and `ecc memory`
ecc python typescript                  # installs ECC rules into ~/.claude/rules/ecc/

# open Claude Code in the repo and trust the folder; it offers to install the
# marketplaces + plugins declared in .claude/settings.json (ecc, ponytail, caveman, obsidian).
claude
# if it does not offer, run once:
#   claude plugin marketplace add affaan-m/ECC            && claude plugin install ecc@ecc --scope project
#   claude plugin marketplace add DietrichGebert/ponytail && claude plugin install ponytail@ponytail --scope project
#   claude plugin marketplace add JuliusBrussee/caveman   && claude plugin install caveman@caveman --scope project
#   claude plugin marketplace add kepano/obsidian-skills  && claude plugin install obsidian@obsidian-skills --scope project
```

Then tell the hooks who you are — create `.claude/settings.local.json` (git-ignored):

```json
{ "env": { "AEGIS_DEV": "A" } }
```
`A` = backend/PayPal/agents, `B` = full stack (frontend + backend)/AG Studio/video. Optional: `"AEGIS_AUTOPUSH": "0"` for pull-only syncing.

Check: start `claude`, you should see `AegisPay: … checklist n/86 · Dev A`. Run `/standup`.

## 2. What runs automatically (hooks — `.claude/settings.json`)

| Event | Script | What it does |
|---|---|---|
| SessionStart | `.claude/hooks/session-start.mjs` | `git pull --rebase --autostash`; injects checklist progress, your next items, the last handoff |
| UserPromptSubmit | `.claude/hooks/route-hint.mjs` | Matches your prompt against `.claude/routing.json`; prints `route → …` and tells Claude which agent/skills/rules to use |
| PostToolUse (Bash/PowerShell) | `.claude/hooks/post-test-sync.mjs` | After a **passing** test command: pull --rebase, push local commits, tell Claude to commit if the tree is dirty |
| PostToolUseFailure | same | After a **failing** test command: tells Claude not to commit or push |
| plugin hooks | ponytail, caveman, ECC | load ponytail + caveman modes each session/subagent; ECC quality, GateGuard, cost tracking, session save |

Test commands recognised: `pytest`, `python -m pytest`, `uv run pytest`, `npm|pnpm|yarn|bun (run) test*`, `vitest`, `jest`, `playwright test`, `make test`.
Pull conflicts abort the rebase and Claude is told which files to resolve — nothing is force-pushed, ever.

## 3. How you know which agent / skill / rule runs — without trusting the AI to guess

Three layers, from most to least deterministic:

1. **Rules by file path (automatic).** `.claude/rules/*.md` have `paths:` frontmatter. Touching `backend/app/settlement/**` loads `money-safety`; `frontend/**` loads `frontend`; etc. No choice involved.
2. **Routing table (automatic, visible).** Every prompt is matched against `.claude/routing.json`. You see the decision in the UI as `route → [money-paypal] agent=ponytail-backend review=… skills=… rules=…`. Claude is instructed to follow it. To change behaviour, edit the JSON — e.g. add `"match": "fixture|recorded response"` to a route.
3. **Explicit override (you decide).** Start the prompt with a slash command (`/ecc:plan`, `/ponytail-review`) or mention an agent (`@agent-ponytail-ai`) and routing is skipped.

Routes today (see the JSON for full lists):

| Route | Triggers on | Implement with | Review with |
|---|---|---|---|
| money-paypal | payout, settle, webhook, ledger, idempotency, cents… | `ponytail-backend` | security-reviewer, python-reviewer |
| ai-decision | langgraph, extract, inject, forensic, eval, prompt, llm… | `ponytail-ai` | security-reviewer, python-reviewer |
| database | postgres, migration, schema, index, sqlalchemy… | `ponytail-backend` | database-reviewer |
| api | fastapi, router, endpoint, sse, login, cors… | `ponytail-backend` | fastapi-reviewer, security-reviewer |
| frontend | react, next.js, widget, ag studio, theme, tsx… | `ponytail-frontend` | react-reviewer, typescript-reviewer |
| e2e | playwright, e2e, scenario S1–S5 | `ecc:e2e-runner` | — |
| build-broken | build failed, type error, cannot find module | `ecc:build-error-resolver` | — |
| plan-design | plan, design, adr, trade-off, spike | `ecc:planner` | `ecc:architect` |
| review-commit | review, before commit/merge, PR | `ecc:code-reviewer` + `/ponytail-review` | security-reviewer |
| memory-sync | standup, handoff, wrap up, resume, what's next | `memory-keeper` | — |
| docs-submission | readme, tools.md, eval.md, devpost, video | `ecc:doc-updater` | — |
| mindmap | mind map, canvas, obsidian, diagram | skill `obsidian:json-canvas` | — |
| security | secret, gitleaks, xss, csrf, credential | `ecc:security-reviewer` | — |

## 4. Invoking ECC directly

| Kind | How to call it | Examples |
|---|---|---|
| Agents | ask for it by name, or `@agent-<name>` in the prompt | `@agent-ecc:planner plan P1-07`, `@agent-ponytail-backend` |
| Main-thread persona | start Claude as that agent | `claude --agent ponytail-backend` (Dev A), `claude --agent ponytail-frontend` or `ponytail-backend` (Dev B) |
| Skills | `/<plugin>:<skill>` or let routing load them | `/ecc:tdd-workflow`, `/ecc:fastapi-patterns`, `/obsidian:json-canvas` |
| Commands | slash commands | `/ecc:plan`, `/ecc:code-review`, `/ecc:security-scan`, `/ecc:test-coverage`, `/ecc:save-session`, `/ecc:resume-session`, `/ecc:harness-audit` |
| Project skills | slash commands | `/standup`, `/handoff` |
| Rules | automatic: `~/.claude/rules/ecc/` (common, python, typescript) + `.claude/rules/` (project, by path) | — |
| Memory | `ecc memory search|save|read` (see [[MEMORY]]) | `ecc memory search "spike"` |

Listing what is available: `/agents`, `/skills`, `/hooks`, `/plugin`, `/memory`. Health check: `/ecc:harness-audit`.

## 5. A normal day

```
claude                       # hook pulls + briefs you
/standup                     # long brief: what the other dev did, your next items
"claim P1-07 and implement the outbox worker"
   → route → [money-paypal] agent=ponytail-backend …   (you see this)
   → ponytail-backend writes the test, then the code; money-safety rule auto-loads
   → pytest passes → hook pulls teammate's work, Claude commits, hook pushes
"review it"                  → route → ecc:code-reviewer + /ponytail-review + security-reviewer
/handoff                     # checklist ticked, team memories saved, log entry, push
```

## 6. Files

```
CLAUDE.md                    agent context (auto-loaded)      AGENTS.md  same, for non-Claude agents
docs/PLAN.md                 phases, owners, contracts        docs/CHECKLIST.md  state (parsed by hooks)
docs/SESSION_LOG.md          handoffs (last entry → next session)
docs/maps/AegisPay.canvas    Obsidian mind map of system + plan
.claude/settings.json        plugins, env, hooks, permissions (shared)
.claude/settings.local.json  your AEGIS_DEV (not committed)
.claude/routing.json         prompt → agent/skills/rules table
.claude/hooks/*.mjs          session-start, route-hint, post-test-sync (+ lib)
.claude/agents/*.md          ponytail-backend, ponytail-frontend, ponytail-ai, memory-keeper
.claude/rules/*.md           money-safety, backend, frontend, ai-agents (path-scoped)
.claude/skills/*/SKILL.md    standup, handoff (+ skills memory-keeper spawns)
.ecc/memory/team/            shared team memory (committed)   .ecc/memory/project/  local only (ignored)
```
