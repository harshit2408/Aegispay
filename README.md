# AegisPay

AegisPay checks supplier invoices against purchase orders, authenticated goods receipts and a human-approved mandate. A LangGraph workflow supplies evidence; deterministic code controls integer-cent payables and locks the receiver to the approved vendor master. A separate settlement worker owns PayPal Payouts and confirms payment by GET before reconciling the supplier invoice.

The application includes an AG Studio cockpit, case evidence/security/money views, mandate controls, authenticated sessions, SSE, demo controls and ops. [CHECKLIST](docs/CHECKLIST.md) holds task state; [EVAL](docs/EVAL.md) records measured checks and limits.

## Judge quickstart: local synthetic replay

**Hosted judge URL and private hosted role access: pending owner verification.** Hosted deployment, final public video and submission remain separate acceptance work. This walkthrough needs no PayPal account, LLM key or AG Studio licence key. Studio may display evaluation/licensing diagnostics; local UI does not establish licensed hosted entitlement.

Prerequisites: Git, **Python 3.12**, **Node.js 22**, Docker, and free localhost ports **55433**, **8100** and **3100**. Linux is the reference environment. On Windows, if Application Control blocks a native Python dependency, use Linux/WSL rather than changing host security settings.

Run in Bash from a clean clone. This disposable database uses a public synthetic password and no persistent volume:

```bash
git clone https://github.com/harshit2408/Aegispay.git
cd Aegispay
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e 'backend[dev]'
docker run --name aegispay-readme-replay-db --detach --rm \
  --publish 127.0.0.1:55433:5432 --env POSTGRES_PASSWORD=postgres \
  --health-cmd='pg_isready -U postgres' --health-interval=2s \
  --health-timeout=3s --health-retries=30 postgres:16
for attempt in {1..30}; do
  docker exec aegispay-readme-replay-db pg_isready -U postgres && break
  sleep 2
done
docker exec aegispay-readme-replay-db pg_isready -U postgres || exit 1
cd frontend
npm ci
npx playwright install --with-deps chromium
npm run test:e2e
```

Chromium's Linux system dependencies may require administrator access. Playwright starts its own Next.js server and API fixture, runs S1–S4, then stops both. No private configuration files are needed or loaded. The fixture creates and drops only its own UUID-named database; its PostgreSQL login needs database-creation privileges.

| Scenario | Expected evidence |
|---|---|
| S1, clean invoice | `AUTO_SETTLE`, payable **$480.00**, vendor-master payee locked |
| S2, rejected goods | `SHORT_PAY`, payable **$425.00**; expected costs **$65.92** versus **$15.00** |
| S3, injection evidence | `FREEZE` with `HIGH_RISK`; authenticated CFO rejects with a reason |
| S4, duplicate | Blocks with a link to the original case |

The suite also checks REPLAY banners, detector-disabled freeze and **zero spend-ledger, PO-ledger and settlement-outbox rows**. It uses real HTTP authentication, CSRF, PostgreSQL, checkpoints and route handlers with synthetic provider responses. Its test-only lifespan replaces production startup/provider/recovery configuration. No settlement worker runs; replay does not prove real payment, provider accuracy or hosted startup. See [browser fixture details](frontend/e2e/README.md).

Keep the database running for a visible browser run:

```bash
npm run test:e2e -- --headed
```

For manual inspection instead, run `python e2e/replay_server.py` in one activated terminal under `frontend/`. In a second terminal under `frontend/`, run:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8100 NEXT_PUBLIC_AG_LICENSE_KEY='' \
  npm run dev -- --hostname localhost --port 3100
```

Open [local cockpit](http://localhost:3100), select **Operator sign in**, and use a fixture account below. Open **Judge guide** for the walkthrough. Use the controller scenario runner to create cases and inspect their Evidence, Security and Money panes. Repeated scenarios can correctly trigger the velocity freeze; restart the fixture for each full rehearsal. Stop both manual servers with Ctrl+C before Playwright, which requires exclusive ports.

### Fixture roles and provisioning

The fixture automatically creates these **local test accounts**, all with public test password `browser-replay-fixture-password`. They exist only in its disposable database; never deploy these passwords.

| Operator ID | Role / use |
|---|---|
| `demo-controller1` | Controller: scenario runner, mandate authoring, eligible case decisions |
| `demo-cfo` | CFO: eligible case decisions, independent activation, settlement resume |
| `demo-admin` | Admin: demo reset/detector and ops; cannot approve payments or activate mandates |
| `demo-receiver` | Receiver: goods receipts |
| `demo-vendor_admin1` | Vendor administrator: vendor-change requests; independent approval requires another eligible operator |

Real operators are `operators` rows with Argon2id hashes and one role each. Existing provisioning is the `seed_demo` callable in [seeder.py](backend/app/demo/seeder.py), used by the fixture. Its `DemoSeed` requires all seven role hashes, a trusted vendor payer ID, distinct supplier/treasury emails and onboarding evidence. It refuses non-demo, replay-enabled and non-sandbox configuration. The fixture supplies synthetic trusted values and invokes it before replay execution. Passing a VendorGateway additionally creates/sends sandbox invoices; seeding never submits payouts.

The owner can provision demo records with `python -m app.demo --config <private-seed.json> --apply-db`; omitting `--apply-db` performs a dry run. The [sandbox S1 runbook](docs/S1_RUNBOOK.md) covers private seed generation and the separate setup service. There is no automatic `OPERATOR_SEED_FILE` loader; that template variable alone does not create accounts. Hosted operators need private passwords; do not publish credentials that satisfy dual approval. Fixture accounts are not hosted judge credentials.

After the walkthrough, stop its named database:

```bash
docker stop aegispay-readme-replay-db
```

`--rm` removes that disposable container. No application volume is created. Failure artifacts under `frontend/test-results/` contain synthetic fixture data.

## Architecture and money controls

- **Decide:** parse, extract, inspect for injection, match trusted records, score risk and apply a deterministic gate. LLM output is evidence; application LLM tools remain read-only.
- **Approve:** sessions supply actor identity. Decisions need a reason and displayed gate hash; fresh facts are checked on resume. A human may lower the payable amount, never raise it or choose a payee.
- **Execute:** an immutable outbox command freezes execution ID, integer cents, receiver and vendor version. The separately privileged worker rechecks halt/trust roots and confirms authoritative PayPal state.
- **Audit:** the worker drains the audit queue into a HMAC chain. External Git anchors and verified exports require private configuration; without it, audit degrades to HMAC-only and cannot prove external truncation detection. See [AUDIT](docs/AUDIT.md).

Rules R1–R25 and the threat model live in [PRD §4](docs/PRD_SRS.md). Payee lock does not prevent a compromised operator pair, database owner, host or falsely recorded goods receipt. Separation of duties, caps and audit bound those risks.

## Simulation boundary

> In production, the supplier's PayPal account belongs to the supplier. In this demo, the Vendor Simulator holds the supplier app's credentials in order to (a) issue invoices, (b) give AegisPay read access to invoices addressed to the treasury — standing in for consented third-party access through PayPal's partner/permissions model — and (c) reconcile invoices after payment, which in production the supplier's own AR system or PayPal's native invoice payment would do. The decision engine only ever receives a **read** interface to supplier data; supplier write calls live in `vendor_sim/` and are never reachable from an LLM.

The `/vendor` page is labelled **Simulator**. Its synthetic preview does not establish authenticated supplier accept/contest integration. Replay and offline tests do not measure live model accuracy. Complete health probes, live reset orchestration, hosted uptime and submission evidence remain tracked in [CHECKLIST](docs/CHECKLIST.md).

### Recorded sandbox acceptance

The 2026-11-12 real sandbox runs are recorded in [S1 evidence](docs/evidence/P1-12-s1.json) and [S2–S4 evidence](docs/evidence/P2-15-s2-s3-s4.json). S1 settled **48,000 cents** and its invoice became `MARKED_AS_PAID`; S2 paid **42,500 cents** and its invoice became `PARTIALLY_PAID`. S3 froze and was rejected with **zero paid**. S4 blocked the duplicate in **41–43 ms**. See [EVAL](docs/EVAL.md) for the measured runs and deviations.

These are individual runs on a free OpenRouter model. S1/S2 ran before the final output-cap and classifier-prompt fixes; S3 used a reference-invoice variant. The detector-OFF and two-human approval paths were not exercised live, and the S2 notice/accept/contest flow remains absent. Held-out model accuracy and hosted acceptance remain unproved.

## Normal development and sandbox deployment

The regular [Compose stack](docker-compose.yml) differs from the browser fixture. It supplies PostgreSQL 16, owner-only migrations and a restricted API login. The `frontend` profile starts Next.js; the `settlement` profile starts the separately privileged worker. Local bootstrap creates capability/runtime database roles. Processes refuse incompatible privileges or secret placement.

[.env.example](.env.example) inventories configuration. Set values privately per service; do not load the combined template into an application process. Startup needs a session secret; real provider/sandbox operations need configured credentials and trusted records. API receives no Treasury credentials or audit keys; settlement receives no LLM/session secrets. The migration owner URL belongs only to a separate administrator session. Never commit credentials or the AG licence key.

Follow [SANDBOX_SETUP](docs/SANDBOX_SETUP.md), [RENDER_SETUP](docs/RENDER_SETUP.md) and [AUDIT](docs/AUDIT.md) for accounts, migrations, deployment and anchors. Historical runbook observations are not fresh acceptance. Changing browser API origin requires a frontend rebuild and matching API `FRONTEND_ORIGIN`. Hosted Strict-cookie sessions require same-site HTTPS frontend/API hosts and configured CORS.

For further checks, keep disposable PostgreSQL running, activate the virtual environment, and run from repository root:

```bash
python -m ruff check backend
python -m pytest backend
node --test .codex/tests/*.test.mjs
python .codex/scripts/sync-claude-context.py --check
cd frontend
npm test
npm run lint
npm run api:check
npm run build
```

`TEST_PG_URL` can select another disposable PostgreSQL base URL without a database path. Database-dependent tests skip locally if PostgreSQL is unavailable; a run with skips is not full acceptance. The browser fixture fails without its database. Runtime OCR also needs Tesseract; replay does not exercise OCR/provider accuracy.

## Repository and team

| Path | Purpose |
|---|---|
| `backend/` | FastAPI, SQLAlchemy/migrations, LangGraph, parsing/forensics, agents, settlement and tests |
| `frontend/` | Next.js App Router, AG Studio widgets, typed API/SSE clients and Playwright |
| `render.yaml` | Render API, worker, frontend and PostgreSQL blueprint |
| `docs/` | Spec, checklist, plan, runbooks, evidence and handoffs |
| `.claude/`, `.codex/`, `.agents/skills/` | Shared harness and native project skills |

Dev A owns PayPal, settlement, agents, forensics and auth. Dev B owns full stack/data, frontend, CI/deployment and video. Read [CHECKLIST](docs/CHECKLIST.md), [PLAN](docs/PLAN.md), [PRD](docs/PRD_SRS.md) and latest [SESSION_LOG](docs/SESSION_LOG.md) before work. Claim items first. Code uses `<a|b>/<ID>-<slug>` branches and PRs with passing CI; R1–R4 changes require other-developer review. Never force-push. End sessions with `$handoff`.

See [Codex setup](docs/harness/CODEX.md), [shared harness](docs/harness/HARNESS.md) and [tools disclosure](docs/TOOLS.md). Studio/AI entitlement through December 15 requires owner evidence; evaluation UI alone is insufficient.

## License

MIT. See [LICENSE](LICENSE).
