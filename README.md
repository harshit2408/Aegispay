# AegisPay

AegisPay lets a small finance team hand vendor-invoice payment to an AI agent that **actually pays through PayPal**, while deterministic code outside the agent's reach — not a prompt — ensures money moves only to a **change-controlled, verified vendor record**, only against an **authenticated acceptance record from an approved source**, and only within a **mandate one human drafted and a second human activated**.

What this does **not** claim: protection against a compromised operator pair, database owner or host, or a goods receipt that an authorised receiver records falsely. Those are bounded by separation of duties, caps and audit (see the threat model in `docs/PRD_SRS.md` §4.0), not prevented.

## Status

Hackathon build for the PayPal hackathon, planned submission 2026-11-11. The repository holds early foundations: the backend skeleton, local Docker setup and CI. Progress, owners and next items are tracked in [docs/CHECKLIST.md](docs/CHECKLIST.md).

### For judges (coming)

_Placeholder. A judge quickstart will be added here (checklist P5-01): the hosted URL, one operator login per role, and the four-scenario walkthrough. Until then, see Local development below._

## How it works

AegisPay separates **deciding** from **executing**.

- **Decide.** A LangGraph workflow ingests the invoice PDF, checks it for hidden text and injection, matches it against the purchase order, the goods receipt and the PayPal invoice, scores risk and applies a deterministic policy gate. LLM steps read untrusted documents and have read-only tools only. They cannot pay anyone.
- **Execute.** An approved case is written to a frozen settlement command (execution ID, amount, payee, vendor version). A separate `settlement` process, which has no LLM and no PDF parsing, is the only code that calls the PayPal Payouts API. Payouts are asynchronous and are confirmed by a `GET` before the invoice is recorded as paid.

Money rules (PRD §4):

- **R1 Payee lock.** The payee comes only from the approved `vendor_master` version. `SettlementService.execute(execution_id)` is the only way money moves.
- **R2 Privilege separation.** No PayPal write tool is visible to any LLM. `paypal/payouts.py` may be imported only from `settlement/`.
- **R3 Amount derivation.** Payable = min(billed, accepted quantity × PO unit price, PO remaining accepted value). A human can lower the amount but never raise it.
- **R4 Integer cents.** Money is integer cents in the database and in Python. `Decimal` is used only when parsing or formatting PayPal strings. Floats near money are a build failure.

Rules R5–R25 (idempotency, write-ahead payouts, HMAC audit chain, roles and separation of duties, sessions and CSRF, and others) are listed in [docs/PRD_SRS.md](docs/PRD_SRS.md) §4. Tests enforce them.

## Simulation boundary

> In production, the supplier's PayPal account belongs to the supplier. In this demo, the Vendor Simulator holds the supplier app's credentials in order to (a) issue invoices, (b) give AegisPay read access to invoices addressed to the treasury — standing in for consented third-party access through PayPal's partner/permissions model — and (c) reconcile invoices after payment, which in production the supplier's own AR system or PayPal's native invoice payment would do. The decision engine only ever receives a **read** interface to supplier data; supplier write calls live in `vendor_sim/` and are never reachable from an LLM.

Out of scope and stated plainly: a compromised host, database owner role or secret store. The external audit anchor (R21) makes such tampering detectable after the fact, not impossible.

## Repository layout

| Path | Status | Purpose |
|---|---|---|
| `LICENSE` | present | MIT |
| `README.md` | present | This file |
| `AGENTS.md`, `CLAUDE.md` | present | Agent context for coding assistants |
| `backend/` | present (skeleton) | FastAPI service, Python 3.12+ |
| `docker-compose.yml` | present | Postgres and api; frontend behind the `frontend` profile |
| `.env.example` | present | Every variable from PRD §7, tagged by service |
| `.github/workflows/ci.yml` | present | ruff, pytest, gitleaks; frontend build once it exists |
| `docs/` | present | PRD, plan, checklist, session log, sandbox setup, tools, harness notes |
| `.claude/` | present | Project agents, hooks, rules and routing for Claude Code |
| `frontend/` | not yet created | Next.js and AG Studio cockpit (checklist items as scheduled) |
| `render.yaml` | target | Render services: api, settlement worker, frontend, Postgres |

The target structure is PRD §6. Directories are created as checklist items need them.

## Local development

Prerequisites: Python 3.12 or newer, Docker (for Postgres and compose), and Node.js later, when the frontend lands.

```bash
# Create and activate a virtual environment (Git Bash / macOS / Linux)
python -m venv .venv
source .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1

# Install the backend with dev tools
pip install -e "backend[dev]"

# Lint and test
ruff check backend
pytest backend
```

Environment and services:

```bash
cp .env.example .env                 # PowerShell: copy .env.example .env
docker compose up                    # postgres + api
# Once the frontend exists:
docker compose --profile frontend up

# Health check (default api port 8000, per NEXT_PUBLIC_API_URL)
curl http://localhost:8000/api/health
```

- PayPal sandbox setup (two Business accounts, two REST apps, webhooks) is documented step by step in [docs/SANDBOX_SETUP.md](docs/SANDBOX_SETUP.md). Put sandbox credentials only in your local `.env`.
- `.env`, keys, sandbox credentials and the AG Studio licence key are never committed. CI runs gitleaks.

## Team workflow

Two developers. Dev A owns backend, PayPal, settlement and agents. Dev B owns frontend, AG Studio, data, CI and deploy. Ownership and the A↔B interface contract are in [docs/PLAN.md](docs/PLAN.md).

Read in this order before starting work:

1. [docs/CHECKLIST.md](docs/CHECKLIST.md): what is done, who is on what, what is next.
2. [docs/PLAN.md](docs/PLAN.md): phases and the interface contract.
3. [docs/PRD_SRS.md](docs/PRD_SRS.md): the spec. Cite its section numbers in commits.
4. The last entry of [docs/SESSION_LOG.md](docs/SESSION_LOG.md): the other developer's handoff.

Working loop:

1. **Claim** a checklist item by appending ` (WIP A)` or ` (WIP B)` to its line, then commit and push.
2. **Work test-first.** Write the failing test, make it pass, then refactor.
3. **Finish** by marking the item `[x]` with the short commit SHA.
4. **End the session** with `/handoff`.

Git rules (PRD §6.1):

- `main` is protected. Pull requests need green CI and one approval.
- Only checklist claims and docs-only edits go directly to `main`.
- Code goes on a branch named `<a|b>/<ID>-<slug>`, opens a PR into `main`, and is squash-merged with a conventional title that cites the checklist ID.
- Changes to R1–R4 money-safety paths need review from the other developer.
- Never force-push. Sync a branch with `git merge origin/main`.

### Claude Code harness

Dev A uses Claude Code. Dev B uses Codex; its project setup, shared rules/memory and
`$standup` / `$handoff` workflows and all four native project agents are documented in [the Codex guide](docs/harness/CODEX.md).

The project enables four plugins in `.claude/settings.json`: ecc, ponytail, caveman and obsidian. Plugins are not installed automatically on each machine, so do this once per machine:

1. Install Node.js before opening Claude Code. The hooks need it.
2. Open the repo in Claude Code and run:
   ```text
   /plugin install ecc@ecc
   /plugin install ponytail@ponytail
   /plugin install caveman@caveman
   /plugin install obsidian@obsidian-skills
   /reload-plugins
   ```
3. Create `.claude/settings.local.json` (git-ignored) with your developer ID:
   ```json
   { "env": { "AEGIS_DEV": "A" } }
   ```
   Use `"B"` on the other machine.

Hooks, routing and the per-tool details are in [docs/harness/HARNESS.md](docs/harness/HARNESS.md).

## License

MIT. See [LICENSE](LICENSE).
