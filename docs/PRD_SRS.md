# AegisPay — Zero-Trust Agentic Payables on PayPal
**Product Requirements Document (PRD) & Software Requirements Specification (SRS) — v2.0**

Revised: 2026-10-21 · Submission deadline: **2026-11-12 12:00 PT (2026-11-13 01:30 IST)** · Planned submission: **2026-11-11** · Team: 2 developers · Judging period: 2026-12-01 → 2026-12-15

> **Instructions for AI coding assistants (Cursor / Windsurf / Claude Code / Copilot)**
> 1. This document is the single source of truth. Follow the directory structure, schemas, state models, API contracts and rules below.
> 2. Any contract tagged **[SPIKE-n]** is an unverified assumption about PayPal sandbox behaviour. Implement only a stub plus a test until the spike result is recorded in §18. Do **not** fill in PayPal behaviour from memory.
> 3. Never give an LLM-reachable component a tool that writes to PayPal. Money moves only through `SettlementService` (§4 rules R1–R3, ADR-001).
> 4. Money is integer cents everywhere (R4). Floats for money are a build failure.
> 5. When generating PayPal client code, use the APIMatic Context Plugin for PayPal in your coding agent and log that usage in `docs/TOOLS.md` (tools disclosure is a submission requirement).

---

## 0. Revision log (v1 → v2)

| # | Area | v1 | v2 | Why |
|---|---|---|---|---|
| 1 | Account model | One PayPal app creates invoices "from" the vendor and also pays them | Two sandbox Business accounts + two REST apps (Treasury, Vendor Simulator) with an explicit simulation boundary (§3) | PayPal requires `invoicer.email_address` to match the Business account behind the access token. The v1 invoice contract fails as written |
| 2 | Shipment Tracking | `trackers-batch` used as the "carrier" leg of the 3-way match | Removed from the critical path. Receiving leg = Goods Receipt (GRN). Orders v2 deposit + `/track` is P2 | `/v1/shipping/trackers-batch` is deprecated; tracking attaches to a capture the *seller* received, which doesn't exist in a pay-on-delivery flow; it is seller-asserted, so it is not independent evidence |
| 3 | Catalog Products | `POST /v1/catalogs/products` as the "PO/catalog" source | Removed | Catalog products carry no price; it added API count, not capability. PayPal judges will spot padding |
| 4 | Payout lifecycle | Record invoice payment "immediately after" the payout POST | Async settlement state machine; reconcile only after item `SUCCESS` (verified webhook or poll); explicit failure paths | Payout creation returns `PENDING`. v1 could mark invoices paid that the vendor never received |
| 5 | Webhooks | Simulator as demo fallback + postback verification | Self-verification (CRC32 of raw body + cert); simulator events flagged `simulated=true` and never advance money state; one subscription per app | Simulator events cannot be verified via the postback endpoint, so the v1 fallback contradicted v1 Rule 4 |
| 6 | Idempotency | Request-Id derived from PO + step; timestamped `sender_batch_id` | Keys derived from `execution_id` + step | PO-derived keys return stale cached responses on demo re-runs; timestamped batch IDs make retries create a second payout |
| 7 | "EV" model | `NetSavings = Δ + $15 + $35`; P(win) never defined; buyer pays a "dispute fee" | Short-Pay Decision Model comparing expected cost of actions with explicit, configurable assumptions (§12) | v1 math double-counts Δ, invents a buyer-side fee, and never makes a decision. Scenario 2 "saves $125" becomes an honest ≈$51 expected saving |
| 8 | Money type | `REAL` / `float` | Integer cents (`BIGINT`) + `Decimal` at boundaries | Float money is an immediate credibility hit with finance-literate judges |
| 9 | Spend cap | `daily_spent_usd` column on the mandate | Atomic reservation ledger (`spend_ledger`) | Concurrent runs could both pass the cap check and overspend |
| 10 | Duplicate payments | Not handled | Unique constraints + fuzzy check + new Scenario S4 | Duplicate payment is the #1 AP control; cheap to build and instantly legible to judges |
| 11 | Human-in-the-loop | Unauthenticated `/resolve`; static `interrupt_before`; `MemorySaver` | Operator auth, mandatory reason, dual approval for high risk, policy gate re-run on resume, dynamic `interrupt()` + Postgres checkpointer | A security product cannot ship an open approval endpoint; frozen threads must survive restarts on Render |
| 12 | Persistence | SQLite locally and on Render | Postgres everywhere except unit tests | Render's filesystem is ephemeral; frozen cases would vanish mid-judging |
| 13 | AI depth | LLM for mandate, extraction, EV narration | + Investigator agent using **read-only PayPal Agent Toolkit** tools; LLM eval set; P1 agent-to-agent short-pay remedy negotiation | "How thoroughly does the project use PayPal and AI" is a scored criterion. v1 listed `mcp_bridge.py` but never used it |
| 14 | Sponsor strategy | AG Grid Enterprise + Render + APIMatic all targeted | One sponsor target: **AG Grid (AG Studio + Studio Agent Framework)**. Render = hosting. APIMatic = dev tooling only | Rules allow at most one sponsor prize per project; the AG Grid brief explicitly asks for AG Studio, custom widgets and the Studio Agent Framework |
| 15 | Product framing | Build spec only | Problem, persona, user stories, metrics, non-goals, NFRs, risks, ADRs, test plan, dated plan, video storyboard | Potential Impact + Design = 40% of the score; judges may score from video and text alone |
| 16 | Submission | Not covered | LICENSE, judge guide, tools disclosure, hosted demo and licences valid through 2026-12-15 | Hard submission requirements |

---

## 1. Product definition

### 1.1 One-liner
AegisPay lets a small finance team hand vendor-invoice payment to an AI agent that **actually pays through PayPal**, while guaranteeing by architecture, not by prompt, that the agent can only pay **verified vendors**, only for **goods actually received**, and only within a **mandate a human signed**.

### 1.2 Problem
1. **Payment fraud targets AP.** The FBI IC3 2025 Internet Crime Report records roughly $3.05B in reported US business email compromise (BEC) losses across 24,768 complaints. *(Link the primary IC3 report in the README; do not cite secondary blogs.)*
2. **Agentic payments widen the attack surface.** Once an LLM reads vendor documents *and* can move money, every inbound PDF is untrusted input that can carry instructions (indirect prompt injection). Prompt-level defences are hygiene, not a payment control.
3. **Small AP teams cannot match every invoice.** With 1–2 people, invoices are either approved without checking PO and receipt (overpayment, duplicates, fraud) or they queue up (late payments, unhappy suppliers). Partial or damaged deliveries are typically paid in full and "chased later".

### 1.3 Target users
- **Primary — Finance controller at a 20–200 person hardware SMB.** Seeded example: a robotics hardware company buying components from dozens of long-tail suppliers, many of them small businesses that bill through **PayPal Invoicing**. AP is 1–2 people.
- **Secondary — The supplier.** A small business on PayPal Invoicing that wants fast payment and a clear explanation when paid short.
- **Validation task (P0, by 2026-10-27):** three 15-minute conversations with people who run AP or finance at SMBs. Record findings in `docs/USER_RESEARCH.md`; use one real quote (with permission) in the video.

### 1.4 User stories and acceptance criteria

| ID | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a controller, I write spending rules in plain English and get a structured, versioned policy that I must explicitly activate. | Compiled values shown as a diff against the active policy; values above org hard limits are clamped and flagged; activation logs actor + version; every execution stores the policy version it used. |
| US-2 | A fully matched invoice inside the mandate is paid through PayPal with no human touch. | Payee taken from vendor master only; amount = PO unit price × accepted qty; UI shows `PAYOUT_SUBMITTED → PAYOUT_SUCCEEDED → SETTLED` with PayPal IDs. |
| US-3 | When part of a delivery is rejected, the agent pays for what was accepted and notifies the supplier. | Decision shows the compared options and their assumptions; supplier receives a notice with the numbers; PayPal invoice shows `PARTIALLY_PAID`. |
| US-4 | Risky invoices freeze with an evidence pack, and I approve, short-pay, or reject. | Reasons + risk breakdown shown; dual approval when risk ≥ threshold; a human can never change the payee or pay more than the PO value of accepted goods. |
| US-5 | A resubmitted invoice is never paid twice. | S4 is blocked within one second with a link to the original execution. |
| US-6 | I can export the full timeline of any payment. | Append-only, hash-chained event log; JSON and CSV export. |
| US-7 | As a supplier, I can see invoice status, payments, and short-pay notices. | Vendor portal (simulator) view with accept/contest actions. |
| US-8 (P1) | I can ask questions about payables in natural language and get a live widget. | AG Studio Studio Agent Framework agent builds a widget from a prompt. |

### 1.5 Success metrics (all demonstrable in the repo or video)

| Metric | Target | Evidence |
|---|---|---|
| Payouts to a receiver not in vendor master | 0 (property-tested) | `docs/EVAL.md` test report |
| Payments without policy version + evidence pack | 0 | Audit export |
| Injection detection on eval set (n ≥ 12 PDFs) | Recall ≥ 0.9, precision ≥ 0.8 (report actuals, not targets) | `docs/EVAL.md` |
| Field extraction accuracy (invoice no., qty, unit price, payee) | ≥ 95% field-level on eval set | `docs/EVAL.md` |
| Decision latency S1 (upload → `APPROVED_FOR_PAYMENT`) | ≤ 20 s p50 | UI timer |
| Settlement confirmation | Shown live; typical sandbox time measured in SPIKE-3 | UI timeline |
| Duplicate invoices paid | 0 | Scenario S4 |

### 1.6 Non-goals (v2)
Live (non-sandbox) PayPal; multi-currency; multi-line POs (schema leaves room, P2); real ERP or mailbox ingestion (upload + seeded inbox only); vendor onboarding/KYC; tax/VAT; non-PayPal payment rails.

---

## 2. Hackathon alignment and prize strategy

### 2.1 Theme fit
- **AI Agents or Automation** — the agent *transacts* (PayPal Payouts), it does not just recommend.
- **Business Solutions** — tooling for SMB finance teams and their suppliers.

### 2.2 Rules that constrain strategy (from the Official Rules)
- A project can win at most **one Grand Prize (1st/2nd/3rd) + one Sponsor Prize**, or **one Honorable Mention + one Sponsor Prize**.
- Judges are not required to run the project and may score from the text, images and video alone → **the video is the product** for most judges.
- The five criteria are equally weighted; a Stage-1 pass/fail viability check comes first.
- Required: public GitHub/GitLab/Bitbucket repo with a detectable open-source license, a working build (hosted URL or complete run instructions, including any sandbox credentials needed), a public YouTube video under 3 minutes, and a tools disclosure.
- Judging runs 2026-12-01 → 2026-12-15, so the hosted demo and every time-limited licence must stay valid until at least 2026-12-15.

### 2.3 Targets
| Tier | Target | Notes |
|---|---|---|
| Grand / Honorable Mention | 1st–3rd Place; Best Use of Agentic Commerce; Best Use of PayPal + AI; (Best Demo Delivery) | Only one can be won |
| Sponsor (primary) | **Best Use of AG Grid** (five cash places) | Brief asks for AG Studio dashboards with custom widgets, theming and layout tools — not AG Grid or AG Charts on their own — and the Studio Agent Framework. Start from the official PayPal + AG Grid boilerplate |
| Used, not optimised | Render (hosting), APIMatic (Context Plugin during development) | Do not build features for these; only one sponsor prize can be won. Claim the APIMatic participant subscription and Render credits anyway |

### 2.4 Judging criteria → evidence map
| Criterion | What judges must see |
|---|---|
| Technological Implementation | Real sandbox payouts and invoice reconciliation across two PayPal apps; verified webhooks; Agent Toolkit read tools inside a LangGraph agent; deterministic settlement; published test and eval results |
| Design | One coherent loop: Inbox → Case → Decision → Settlement receipt; supplier-side view; AG Studio cockpit with custom widgets and a consistent theme |
| Potential Impact | Named persona + real quotes; IC3 statistic; duplicate, short-pay and BEC handled; honest savings math |
| Innovation | Injection-aware agent payments with an architectural payee lock; NL mandate → versioned guardrails; (P1) agent-to-agent short-pay remedy negotiation |
| Presentation | Storyboarded video (§20) with live sandbox evidence and a "kill-switch" proof that safety does not depend on the detector |

---

## 3. Actor and PayPal account model (NEW — read before writing any PayPal code)

| Actor | Sandbox account | REST app | Env prefix | Allowed PayPal operations |
|---|---|---|---|---|
| Treasury (the AegisPay customer, i.e. the buyer) | Business account `treasury@…` | `aegispay-treasury` | `TREASURY_` | Payouts: create, get batch, get item, cancel unclaimed item. Webhooks: payout events. Agent Toolkit: `list_transactions` (read) |
| Supplier (Vendor Simulator) | Business account `supplier@…` | `aegispay-vendor-sim` | `VENDOR_` | Invoicing: create, send, get, list, record payment. Webhooks: invoicing events. Agent Toolkit: `get_invoice`, `list_invoices` (read) |
| Attacker | **None** — only an email string inside a PDF | — | — | — |

**Simulation boundary (copy verbatim into the README):**
> In production, the supplier's PayPal account belongs to the supplier. In this demo, the Vendor Simulator holds the supplier app's credentials in order to (a) issue invoices, (b) give AegisPay read access to invoices addressed to the treasury — standing in for consented third-party access through PayPal's partner/permissions model — and (c) reconcile invoices after payment, which in production the supplier's own AR system or PayPal's native invoice payment would do. The decision engine only ever receives a **read** interface to supplier data; supplier write calls live in `vendor_sim/` and are never reachable from an LLM.

[SPIKE-2] decides whether the Treasury app can read invoices addressed to it directly. If yes, use Treasury credentials for reads and narrow the simulation boundary to (a) and (c).

---

## 4. Critical engineering rules (must be enforced in code and tests)

| ID | Rule | Enforced by |
|---|---|---|
| **R1 — Payee lock** | The payout receiver is always `vendor_master.verified_paypal_email` (or `paypal_payer_id`, see Q1). No code path accepts a payee from an LLM, a PDF, a PayPal invoice, or a human request body. | `SettlementService.execute(execution_id)` takes **only** an execution ID; property test (§17) |
| **R2 — Privilege separation** | LLM-reachable components get read-only tools only. No PayPal write tool exists in any tool registry an LLM can see. | Startup assertion over the tool registry; import-boundary test: `paypal/payouts.py` may be imported only from `settlement/` |
| **R3 — Amount derivation** | The amount paid is computed deterministically: `accepted_qty × PO unit price` in cents, and never exceeds the billed amount. Extracted amounts are evidence only. A human approval can lower but never raise it. | `policy_gate.payable_cents()`; property test |
| **R4 — Money type** | Integer minor units (cents) in DB and Python; `Decimal` only when formatting PayPal strings like `"480.00"`. | Type hints + `ruff` rule banning `float` in `schemas/` and `settlement/` |
| **R5 — Idempotency** | `PayPal-Request-Id = f"aegis-{execution_id}-{step}"`; `sender_batch_id = f"AEGIS-{execution_id}"`; `sender_item_id = f"{execution_id}-1"`. Re-running a scenario creates a **new** `execution_id` and a new vendor invoice number. Retries reuse the same keys. | `paypal/client.py` requires `request_id` on every POST |
| **R6 — Async payouts** | Payout creation returns `PENDING`. The execution moves to `PAYOUT_SUBMITTED`; only a verified `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` webhook **or** a poll showing item `SUCCESS` moves it to `PAYOUT_SUCCEEDED` and triggers invoice reconciliation. Failure statuses release the spend reservation. | `settlement/reconciler.py` state machine; chaos tests |
| **R7 — Vendor-side reconciliation** | `POST /v2/invoicing/invoices/{id}/payments` is called with **Vendor** credentials via `VendorGateway`, after payout success. | `vendor_sim/gateway.py` is the only caller |
| **R8 — Atomic spend cap** | Before a payout, reserve the amount in `spend_ledger` inside a transaction that locks the active mandate row (`SELECT … FOR UPDATE`). Release on failure/reject. The daily window uses the mandate's `cap_timezone`. | `settlement/spend_ledger.py`; concurrency test |
| **R9 — Duplicate prevention** | Unique `(vendor_id, vendor_invoice_number)` and unique `paypal_invoice_id` among non-rejected executions, plus a fuzzy check (same vendor, same amount, within 30 days, invoice number edit distance ≤ 2) that freezes. | DB partial unique indexes + `match` node |
| **R10 — Webhook verification** | Verify every webhook from the **raw request bytes**: CRC32 of the body + transmission headers + webhook ID, signature checked against the certificate at `paypal-cert-url` (reject unless the cert host ends in `.paypal.com`). Postback verification is allowed for real events only. Simulator events verify with webhook ID `WEBHOOK_ID`, are stored with `simulated=true`, and **never** change money state. | `paypal/webhooks.py`; tests with recorded headers |
| **R11 — HITL integrity** | Decision endpoints require an operator session; the actor comes from the session, never the request body; a reason is mandatory; resume always re-runs the policy gate; dual approval (two distinct operators) is required when `risk ≥ dual_approval_risk_threshold`; duplicates and over-cap cases cannot be approved. | `routers/executions.py`, `decision/policy_gate.py` |
| **R12 — Distinct sandbox accounts** | Treasury and Supplier are different sandbox accounts (prevents `CANNOT_PAY_SELF`). | `/api/health` check |
| **R13 — Mandate integrity** | LLM-compiled policy → Pydantic validation → clamp to `ORG_HARD_*` limits → shown as a diff → explicit activation → immutable versioned row. | `routers/mandates.py`; tests for clamping |
| **R14 — LLM isolation** | Extraction and classification LLM calls have **zero** tools; document text is passed as delimited data; outputs are schema-validated; numbers in any LLM-drafted message are filled by template, not generated. | `forensics/`, `vendor_sim/notices.py` |

---

## 5. Architecture

### 5.1 Component view

```text
┌──────────────────────── Next.js (App Router) ────────────────────────┐
│ AG Studio cockpit (custom widgets) · Case view · Mandate studio ·     │
│ Vendor portal (simulator) · Judge guide                               │
└──────────────▲───────────────────────────────────┬────────────────────┘
          SSE  │                                   │ REST (operator session cookie)
┌──────────────┴───────────────────────────────────▼────────────────────┐
│ FastAPI                                                               │
│                                                                       │
│  DECIDE  (LLMs allowed, read-only)        EXECUTE  (no LLM)           │
│  ┌──────── LangGraph ─────────┐  outbox   ┌──── SettlementService ───┐│
│  │ ingest → forensics → match │ ────────► │ reserve spend            ││
│  │ → score_risk → investigate?│  command  │ → POST payout (PENDING)  ││
│  │ → short_pay_decide         │           │ ← webhook / poll SUCCESS ││
│  │ → policy_gate              │           │ → VendorGateway: record  ││
│  │ → human_review (interrupt) │           │   invoice payment        ││
│  │ → approve_for_payment      │           │ → SETTLED                ││
│  └────────────┬───────────────┘           └───────────┬──────────────┘│
│  read-only Agent Toolkit tools            Treasury app │ Vendor app    │
└───────────────┼────────────────────────────────────────┼──────────────┘
                ▼                                        ▼
        PayPal sandbox (Invoicing — Vendor app)   PayPal sandbox (Payouts — Treasury app)
                └──────── verified webhooks (two subscriptions) ────────┘

Postgres: domain tables · LangGraph checkpoints · append-only events · settlement outbox
```

### 5.2 Architecture Decision Records (store each in `docs/adr/`)

**ADR-001 — Decide/Execute split.**
*Context:* LLM nodes read attacker-controlled documents. *Decision:* LangGraph decides; a deterministic `SettlementService` executes money movement from an outbox command containing only an `execution_id`. *Positive:* injection cannot reach money movement even if every detector fails; settlement is retryable and testable. *Negative:* two state machines to keep consistent. *Alternatives:* tool-calling agent with "approval tools" (rejected — the agent could still construct payout arguments).

**ADR-002 — Postgres everywhere.**
*Context:* Render's filesystem is ephemeral; frozen threads must survive restarts during judging. *Decision:* Render Postgres for domain data and `AsyncPostgresSaver` checkpoints; SQLite only in unit tests. *Negative:* local setup needs Docker. *Alternatives:* SQLite on a persistent disk (rejected — weaker concurrency for R8).

**ADR-003 — Remove Catalog Products; demote Shipment Tracking to P2.**
*Context:* Catalog products carry no price; v1 tracking is deprecated, needs a prior capture, and is seller-asserted. *Decision:* the 3-way match is PO ↔ Invoice (PDF + PayPal invoice) ↔ Goods Receipt. Tracking returns only with the P2 deposit flow via Orders v2 `/track`. *Negative:* fewer PayPal endpoints on the slide. *Positive:* every PayPal call does real work.

**ADR-004 — One sponsor target: AG Studio.**
*Context:* Only one sponsor prize can be won. *Decision:* invest in AG Studio custom widgets, theming and a Studio Agent Framework agent; use Render and APIMatic without building for them. *Negative:* AG Studio learning curve and licence-term risk (SPIKE-7).

**ADR-005 — Dynamic interrupts + durable checkpointer.**
*Decision:* `interrupt()` inside `human_review` and `Command(resume=…)` on decision; `human_review` always routes back to `policy_gate`. Static `interrupt_before` is not used.

**ADR-006 — Integer cents.**
*Decision:* `BIGINT` cents in DB, `int` in Pydantic, `Decimal` only for PayPal string formatting.

---

## 6. Repository structure (monorepo)

```text
aegispay/
├── LICENSE                         # MIT or Apache-2.0 — must show in GitHub's About panel
├── README.md                       # Judge quickstart, hosted URL, operator logins, simulation boundary, video link
├── render.yaml                     # api (web), frontend (web), postgres
├── docker-compose.yml              # postgres + api + frontend for local dev
├── .env.example
├── docs/
│   ├── PRD_SRS.md                  # this file
│   ├── TOOLS.md                    # AI models + IDs, PayPal APIs, Agent Toolkit, APIMatic Context Plugin, AG Studio
│   ├── EVAL.md                     # forensics + extraction eval results, property-test report
│   ├── SANDBOX_SETUP.md            # 2 Business accounts, 2 REST apps, 2 webhooks, step by step
│   ├── USER_RESEARCH.md
│   └── adr/                        # ADR-001 … ADR-006
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                 # FastAPI app, CORS (frontend origin only), routers, SSE
│   │   ├── config.py               # pydantic-settings
│   │   ├── db.py                   # async SQLAlchemy 2.0 engine/session
│   │   ├── auth.py                 # operator sessions (controller, cfo, admin)
│   │   ├── models/                 # ORM tables (§8)
│   │   ├── schemas/                # domain.py, state.py, api.py (§11)
│   │   ├── paypal/
│   │   │   ├── client.py           # httpx; OAuth token cache per app; Request-Id; logs paypal-debug-id
│   │   │   ├── payouts.py          # TREASURY app — importable only from settlement/
│   │   │   ├── invoicing.py        # VENDOR app — importable only from vendor_sim/ and read adapters
│   │   │   ├── webhooks.py         # self-verification + postback
│   │   │   └── toolkit.py          # PayPal Agent Toolkit, read-only configuration
│   │   ├── decision/               # graph.py, nodes/, policy_gate.py, risk.py, short_pay.py
│   │   ├── forensics/              # pdf_structure.py, ocr_diff.py, classifier.py
│   │   ├── settlement/             # service.py, outbox_worker.py, reconciler.py, spend_ledger.py
│   │   ├── vendor_sim/             # gateway.py, notices.py, negotiator.py (P1)
│   │   ├── audit/                  # events.py (hash chain), export.py
│   │   ├── demo/                   # seeder.py, scenarios.py, pdf_factory.py, replay.py
│   │   └── routers/                # auth, mandates, documents, executions, webhooks, vendor_sim, demo, health
│   └── tests/
│       ├── unit/ property/ contract/ eval/ e2e/
│       └── fixtures/               # recorded sandbox responses, eval PDFs + labels
└── frontend/
    ├── package.json
    └── src/
        ├── app/page.tsx            # AG Studio cockpit
        ├── app/cases/[id]/page.tsx
        ├── app/mandate/page.tsx
        ├── app/vendor/page.tsx
        ├── widgets/                # AG Studio custom widgets (§14.2)
        ├── components/
        └── lib/api.ts, lib/sse.ts  # typed client generated from FastAPI OpenAPI
```

Removed from v1: `mcp_bridge.py` (replaced by `paypal/toolkit.py`), `AgentGraphView` as a standalone component (now a widget), Catalog seeding.

---

## 7. Environment configuration (`.env.example`)

```ini
# --- PayPal sandbox (shared) ---
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com

# --- Treasury (buyer) REST app: Payouts ---
TREASURY_CLIENT_ID=
TREASURY_CLIENT_SECRET=
TREASURY_WEBHOOK_ID=
TREASURY_ACCOUNT_EMAIL=treasury@aegispay-demo.example     # Business sandbox account behind this app

# --- Vendor Simulator REST app: Invoicing ---
VENDOR_CLIENT_ID=
VENDOR_CLIENT_SECRET=
VENDOR_WEBHOOK_ID=
VENDOR_ACCOUNT_EMAIL=supplier@aegispay-demo.example       # MUST equal invoicer.email_address

# --- LLM (pick ONE provider; record exact model IDs in docs/TOOLS.md) ---
LLM_PROVIDER=
LLM_API_KEY=
LLM_MODEL_EXTRACT=          # vision-capable
LLM_MODEL_REASON=

# --- Database ---
DATABASE_URL=postgresql+asyncpg://aegis:aegis@localhost:5432/aegis
CHECKPOINT_DB_URL=postgresql://aegis:aegis@localhost:5432/aegis

# --- Auth (demo operators; real values only in Render env) ---
SESSION_SECRET=
OPERATOR_CONTROLLER_PASSWORD=
OPERATOR_CFO_PASSWORD=
OPERATOR_ADMIN_PASSWORD=

# --- Org hard limits (LLM-compiled mandates are clamped to these) ---
ORG_HARD_MAX_SINGLE_CENTS=100000
ORG_HARD_DAILY_CAP_CENTS=500000

# --- Demo controls ---
FORENSICS_DETECTOR_ENABLED=true     # admin-toggleable at runtime for the kill-switch proof
DEMO_REPLAY_MODE=false              # true = recorded sandbox responses, UI shows a REPLAY banner

# --- Frontend ---
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_AG_LICENSE_KEY=         # set in Render env only; never commit
```

Secrets policy: `.env` is git-ignored; CI runs `gitleaks`; sandbox credentials are rotated after judging.

---

## 8. Data model (Postgres, SQLAlchemy 2.0 `DeclarativeBase`)

```sql
-- Mandates are immutable and versioned. Exactly one active version.
CREATE TABLE mandate_policies (
    policy_id                    TEXT        NOT NULL,
    version                      INTEGER     NOT NULL,
    raw_nl_prompt                TEXT        NOT NULL,
    compiled_json                JSONB       NOT NULL,   -- full validated MandatePolicy
    clamped_fields               JSONB       NOT NULL DEFAULT '[]',
    max_single_invoice_cents     BIGINT      NOT NULL CHECK (max_single_invoice_cents >= 0),
    daily_cap_cents              BIGINT      NOT NULL CHECK (daily_cap_cents >= 0),
    cap_timezone                 TEXT        NOT NULL DEFAULT 'UTC',
    max_price_variance_bps       INTEGER     NOT NULL DEFAULT 0,
    auto_short_pay_enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
    max_short_pay_delta_cents    BIGINT      NOT NULL DEFAULT 10000,
    dual_approval_risk_threshold INTEGER     NOT NULL DEFAULT 80,
    is_active                    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by                   TEXT        NOT NULL,
    activated_by                 TEXT,
    activated_at                 TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, version)
);
CREATE UNIQUE INDEX one_active_mandate ON mandate_policies (is_active) WHERE is_active;

CREATE TABLE vendor_master (
    vendor_id               TEXT PRIMARY KEY,
    vendor_name             TEXT NOT NULL,
    verified_paypal_email   TEXT NOT NULL UNIQUE,
    paypal_payer_id         TEXT UNIQUE,                 -- optional stronger binding (Q1)
    trust_status            TEXT NOT NULL DEFAULT 'VERIFIED'
                            CHECK (trust_status IN ('VERIFIED','PENDING_CHANGE','BLOCKED')),
    payee_last_changed_at   TIMESTAMPTZ
);

CREATE TABLE purchase_orders (           -- single line per PO in v2 (multi-line is P2)
    po_id             TEXT PRIMARY KEY,
    vendor_id         TEXT   NOT NULL REFERENCES vendor_master(vendor_id),
    sku_id            TEXT   NOT NULL,
    item_name         TEXT   NOT NULL,
    ordered_qty       INTEGER NOT NULL CHECK (ordered_qty > 0),
    unit_price_cents  BIGINT NOT NULL CHECK (unit_price_cents >= 0),
    currency          TEXT   NOT NULL DEFAULT 'USD',
    status            TEXT   NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED'))
);

CREATE TABLE goods_receipts (            -- the "receiving" leg of the 3-way match
    grn_id            TEXT PRIMARY KEY,
    po_id             TEXT    NOT NULL REFERENCES purchase_orders(po_id),
    received_qty      INTEGER NOT NULL,
    accepted_qty      INTEGER NOT NULL,
    rejected_qty      INTEGER NOT NULL,
    inspection_note   TEXT,
    recorded_by       TEXT    NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL,
    CHECK (accepted_qty + rejected_qty = received_qty)
);

CREATE TABLE invoice_documents (
    document_id         TEXT PRIMARY KEY,
    source              TEXT NOT NULL CHECK (source IN ('PDF_UPLOAD','SEEDED_INBOX')),
    sha256              TEXT NOT NULL,
    storage_path        TEXT NOT NULL,
    extraction_json     JSONB,             -- InvoiceExtraction (evidence only)
    forensics_json      JSONB,             -- ForensicsReport
    uploaded_by         TEXT NOT NULL,
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE invoice_executions (
    execution_id           TEXT PRIMARY KEY,
    document_id            TEXT   NOT NULL REFERENCES invoice_documents(document_id),
    po_id                  TEXT   REFERENCES purchase_orders(po_id),
    vendor_id              TEXT   REFERENCES vendor_master(vendor_id),
    vendor_invoice_number  TEXT,
    paypal_invoice_id      TEXT,
    langgraph_thread_id    TEXT   NOT NULL UNIQUE,
    policy_id              TEXT   NOT NULL,
    policy_version         INTEGER NOT NULL,
    billed_cents           BIGINT,
    approved_cents         BIGINT,
    route                  TEXT CHECK (route IN ('AUTO_SETTLE','SHORT_PAY','FREEZE','DUPLICATE_BLOCKED')),
    status                 TEXT NOT NULL CHECK (status IN (
                              'RECEIVED','ANALYZING','FROZEN','REJECTED','DUPLICATE_BLOCKED',
                              'APPROVED_FOR_PAYMENT','PAYOUT_SUBMITTED','PAYOUT_SUCCEEDED',
                              'PAYOUT_FAILED','SETTLED')),
    risk_score             INTEGER CHECK (risk_score BETWEEN 0 AND 100),
    match_report_json      JSONB,
    risk_json              JSONB,
    evidence_pack_json     JSONB,
    short_pay_json         JSONB,
    short_pay_balance      TEXT CHECK (short_pay_balance IN ('NONE','NOTICE_SENT','ACCEPTED','CONTESTED')) DEFAULT 'NONE',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (policy_id, policy_version) REFERENCES mandate_policies(policy_id, version)
);
-- R9: never two live executions for the same vendor invoice
CREATE UNIQUE INDEX uq_live_vendor_invoice ON invoice_executions (vendor_id, vendor_invoice_number)
    WHERE status NOT IN ('REJECTED','DUPLICATE_BLOCKED') AND vendor_invoice_number IS NOT NULL;
CREATE UNIQUE INDEX uq_live_paypal_invoice ON invoice_executions (paypal_invoice_id)
    WHERE status NOT IN ('REJECTED','DUPLICATE_BLOCKED') AND paypal_invoice_id IS NOT NULL;

CREATE TABLE approvals (
    approval_id     TEXT PRIMARY KEY,
    execution_id    TEXT NOT NULL REFERENCES invoice_executions(execution_id),
    actor           TEXT NOT NULL,           -- from session, never from request body
    action          TEXT NOT NULL CHECK (action IN ('APPROVE','SHORT_PAY','REJECT')),
    reason          TEXT NOT NULL CHECK (length(reason) >= 10),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (execution_id, actor)             -- dual approval needs two distinct operators
);

CREATE TABLE spend_ledger (               -- R8: today's committed = Σ RESERVE − Σ RELEASE
    entry_id        BIGSERIAL PRIMARY KEY,
    execution_id    TEXT   NOT NULL REFERENCES invoice_executions(execution_id),
    policy_id       TEXT   NOT NULL,
    business_date   DATE   NOT NULL,         -- in the mandate's cap_timezone
    kind            TEXT   NOT NULL CHECK (kind IN ('RESERVE','RELEASE')),
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE settlement_outbox (
    command_id      TEXT PRIMARY KEY,
    execution_id    TEXT NOT NULL UNIQUE REFERENCES invoice_executions(execution_id),
    status          TEXT NOT NULL CHECK (status IN ('PENDING','CLAIMED','DONE','FAILED')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payouts (
    payout_id         TEXT PRIMARY KEY,
    execution_id      TEXT   NOT NULL UNIQUE REFERENCES invoice_executions(execution_id),
    sender_batch_id   TEXT   NOT NULL UNIQUE,
    payout_batch_id   TEXT   UNIQUE,
    payout_item_id    TEXT   UNIQUE,
    receiver          TEXT   NOT NULL,       -- copied from vendor_master at submit time
    amount_cents      BIGINT NOT NULL,
    batch_status      TEXT,
    item_status       TEXT,
    last_polled_at    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE execution_events (           -- append-only, hash-chained audit trail
    event_seq       BIGSERIAL PRIMARY KEY,
    execution_id    TEXT  NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT  NOT NULL,          -- system | llm:<model-id> | operator:<id> | paypal:webhook
    event_type      TEXT  NOT NULL,
    payload         JSONB NOT NULL,
    prev_hash       TEXT  NOT NULL,
    hash            TEXT  NOT NULL           -- sha256(prev_hash || canonical_json(row without hash))
);
-- Application role has INSERT/SELECT only on execution_events (no UPDATE/DELETE).

CREATE TABLE webhook_events (
    event_id            TEXT PRIMARY KEY,    -- PayPal event id → natural dedupe
    app                 TEXT NOT NULL CHECK (app IN ('TREASURY','VENDOR')),
    event_type          TEXT NOT NULL,
    resource_id         TEXT NOT NULL,
    signature_verified  BOOLEAN NOT NULL,
    simulated           BOOLEAN NOT NULL DEFAULT FALSE,
    headers_json        JSONB NOT NULL,
    raw_body            BYTEA NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ
);
```

Removed from v1: `daily_spent_usd` (derived from `spend_ledger`), `paypal_capture_tx_id` / `tracking_number` / `carrier` on POs (ADR-003), `warehouse_*` columns on POs (now `goods_receipts`), the `WEBHOOK_VERIFIED` status (verification is a property of webhook events, not an execution status), and the duplicate price source on `vendor_master`.

---

## 9. PayPal sandbox API contracts

All calls go to `PAYPAL_BASE_URL`. Every POST carries `PayPal-Request-Id` (R5). Every response's `paypal-debug-id` header is logged with the `execution_id`.

### 9.1 OAuth (one token cache per app)
`POST /v1/oauth2/token` with HTTP Basic (`<APP>_CLIENT_ID`, `<APP>_CLIENT_SECRET`), body `grant_type=client_credentials`. Cache per app; refresh when `now >= expires_at - 300 s`.

### 9.2 Invoicing v2 — **Vendor app only** (`vendor_sim/gateway.py`)

**Create draft** — `POST /v2/invoicing/invoices`
```json
{
  "detail": {
    "invoice_number": "INV-2026-1042-<run>",
    "reference": "PO-1042",
    "currency_code": "USD",
    "note": "Restock shipment for PO-1042",
    "payment_term": { "term_type": "NET_10" }
  },
  "invoicer": { "email_address": "<VENDOR_ACCOUNT_EMAIL>" },
  "primary_recipients": [
    { "billing_info": { "email_address": "<TREASURY_ACCOUNT_EMAIL>" } }
  ],
  "items": [
    {
      "name": "Industrial LiDAR Sensor Bracket",
      "quantity": "100",
      "unit_amount": { "currency_code": "USD", "value": "4.80" },
      "unit_of_measure": "QUANTITY"
    }
  ],
  "configuration": {
    "partial_payment": {
      "allow_partial_payment": true,
      "minimum_amount_due": { "currency_code": "USD", "value": "50.00" }
    }
  }
}
```
- `invoicer.email_address` **must** equal the Business account behind the Vendor app's token [SPIKE-1].
- `<run>` is a 4-hex-char suffix generated per demo reset so invoice numbers never collide.

**Send** — `POST /v2/invoicing/invoices/{invoice_id}/send` with `{"send_to_invoicer": false, "send_to_recipient": false}`.

**Read** — `GET /v2/invoicing/invoices/{invoice_id}`; list/search for the investigator's read tools. Whether Treasury credentials can read invoices addressed to the treasury is [SPIKE-2].

**Record payment (after payout success only, R6/R7)** — `POST /v2/invoicing/invoices/{invoice_id}/payments`
```json
{
  "method": "PAYPAL",
  "payment_date": "2026-11-05",
  "amount": { "currency_code": "USD", "value": "425.00" },
  "note": "AegisPay payout batch <payout_batch_id> item <payout_item_id>; short-pay notice <notice_id>"
}
```
Full amount → `PAID`; accepted-value amount on a short-pay → `PARTIALLY_PAID` with the balance tracked in `short_pay_balance` [SPIKE-4: confirm statuses and which app receives `INVOICING.INVOICE.PAID`].

### 9.3 Payouts v1 — **Treasury app only** (`settlement/`)

**Create** — `POST /v1/payments/payouts`
```json
{
  "sender_batch_header": {
    "sender_batch_id": "AEGIS-<execution_id>",
    "email_subject": "Payment for INV-2026-1042-<run> (PO-1042)",
    "email_message": "Paid by AegisPay after PO and goods-receipt match."
  },
  "items": [
    {
      "recipient_type": "EMAIL",
      "receiver": "<vendor_master.verified_paypal_email>",
      "amount": { "value": "480.00", "currency": "USD" },
      "sender_item_id": "<execution_id>-1",
      "note": "INV-2026-1042-<run> / PO-1042 / policy v<version>"
    }
  ]
}
```
Response is asynchronous: `batch_status = PENDING` → execution `PAYOUT_SUBMITTED`.

**Status** — `GET /v1/payments/payouts/{payout_batch_id}` and `GET /v1/payments/payouts-item/{payout_item_id}`. The reconciler polls every 15 s for up to 30 min as a fallback to webhooks [SPIKE-3: measure real sandbox timings].

**Item status handling:**

| Item status | Action |
|---|---|
| `SUCCESS` | `PAYOUT_SUCCEEDED` → VendorGateway record payment → `SETTLED` |
| `PENDING`, `ONHOLD` | Keep polling; show in UI |
| `UNCLAIMED` | Freeze with reason `STALE_PAYEE`; operator may `POST /v1/payments/payouts-item/{id}/cancel`; release reservation after cancel |
| `FAILED`, `RETURNED`, `BLOCKED`, `DENIED`, `REFUNDED`, `REVERSED` | `PAYOUT_FAILED`; release reservation; surface "retry after vendor-master review" |

### 9.4 Webhooks v1
- Two subscriptions: Treasury app → `PAYMENT.PAYOUTSBATCH.*`, `PAYMENT.PAYOUTS-ITEM.*`; Vendor app → `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.UPDATED`, `INVOICING.INVOICE.CANCELLED`. Confirm exact names in the dashboard during SPIKE-4.
- Endpoints: `/api/webhooks/paypal/treasury`, `/api/webhooks/paypal/vendor`.
- Verification per R10. Self-verification message: `<transmission_id>|<transmission_time>|<webhook_id>|<crc32(raw_body) as unsigned decimal>`, signature algorithm from `paypal-auth-algo`, certificate from `paypal-cert-url`.
- Local development needs a public HTTPS tunnel for the listener URL.
- Webhooks are an accelerator, not a dependency: the reconciler poll (§9.3) guarantees progress.

### 9.5 PayPal Agent Toolkit — read-only (`paypal/toolkit.py`) [SPIKE-6]
- Instantiate one toolkit per credential set with **read actions only** (invoices: get/list; transactions: list).
- At startup, assert that no exposed tool name matches `create|send|cancel|update|refund|capture|payout`; fail to boot otherwise (R2).
- Used by the Investigator agent (§10, A4). Never bound to any other node.
- Fallback if the Python package can't be used with LangGraph: wrap our own read methods as LangChain tools with identical names, and say so in `docs/TOOLS.md`.

### 9.6 Removed / deferred
- `POST /v1/catalogs/products` — removed (ADR-003).
- `/v1/shipping/trackers-batch`, `/v1/shipping/trackers/{id}` — removed (deprecated; ADR-003).
- **P2:** deposit flow — Vendor app creates an Orders v2 order for a 10% deposit, a sandbox buyer approves it, Vendor captures, then adds tracking via `POST /v2/checkout/orders/{id}/track` with the `capture_id`. Tracking is then shown as a *seller-asserted* signal, never as proof of receipt.

---

## 10. AI components

| ID | Component | Kind | Input | Output (schema) | Tools | Guardrails | On failure |
|---|---|---|---|---|---|---|---|
| A1 | Mandate Compiler | Structured-output LLM | Controller's NL rules | `MandatePolicy` draft | None | Pydantic validation; clamp to `ORG_HARD_*`; diff + explicit activation (R13) | Show validation errors; nothing activates |
| A2 | Invoice Extractor | Vision + text LLM | Rendered pages + text layer | `InvoiceExtraction` | None | Evidence only (R3); field confidence; delimited untrusted input (R14) | `FREEZE` reason `EXTRACTION_LOW_CONFIDENCE` |
| A3 | Injection Classifier | LLM + deterministic detectors | Hidden-text segments + full text as data | `ForensicsReport` | None | Never sees tools; detector results are independent of the LLM | Treat as `SUSPICIOUS` |
| A4 | Investigator | ReAct agent, ≤ 6 tool calls | Match report, risk breakdown | `EvidencePack` | Read-only Agent Toolkit + internal read tools (vendor history, PO, prior executions) | Every fact must cite a tool-call ID or it is dropped by a validator; recommendation is advisory and ignored by the gate | Case shows "investigation unavailable" |
| A5 | Notice Drafter | LLM | `ShortPayDecision`, `GateDecision` | Notice text with `{placeholders}` | None | Numbers inserted by template after generation (R14) | Fallback static template |
| A6 (P1) | Vendor Agent (simulator side) | LLM | Short-pay notice | `RemedyProposal` | None | Can only choose among structured remedies (§12.4) | Escalate to human |
| A7 (P1) | Treasury Analyst | AG Studio Studio Agent Framework | NL question | Widget spec | Read-only executions dataset | No write access | Show error toast |

### 10.1 Forensics: deterministic hidden-text detection (`forensics/pdf_structure.py`, `ocr_diff.py`)
Detect and report each finding with page number and bounding box (for the UI overlay):
- text whose fill colour is within ΔE < 3 of the background beneath it (white-on-white);
- font size < 2 pt;
- text outside the page's crop box;
- invisible render mode, or text covered by an opaque shape;
- content in annotations, form fields, metadata/XMP, embedded files or JavaScript;
- **OCR-vs-text-layer diff:** render each page (e.g., 200 DPI), OCR it, and flag text-layer tokens that do not appear in the OCR output.

Libraries: `pdfplumber` (MIT) and `pypdfium2` (Apache-2.0/BSD) for parsing and rendering; Tesseract or the vision model for OCR. Avoid AGPL-licensed PDF libraries unless the repo itself is AGPL.

**Eval set (`tests/fixtures/eval/`, ≥ 12 PDFs):** clean invoices; white-on-white; 1-pt text; off-page text; annotation payload; metadata payload; look-alike payee only (no injection); and **false-positive traps** (legitimate footers such as "ignore this notice if already paid"). Publish precision/recall and field-level extraction accuracy in `docs/EVAL.md`.

### 10.2 Deterministic risk score (`decision/risk.py`)
Additive, capped at 100, every contribution shown in the UI.

| Finding | Points |
|---|---|
| Classifier = `INJECTION` | 60 |
| Classifier = `SUSPICIOUS` | 25 |
| Any hidden-text segment present | 15 |
| Extracted payee ≠ vendor master | 40 |
| Extracted payee is a look-alike of vendor master (edit distance ≤ 2 or homoglyph) | +10 |
| No PayPal invoice from the vendor's account for this invoice number | 15 |
| Unit price variance above tolerance | 20 |
| Billed qty > ordered qty | 20 |
| Fuzzy duplicate | 30 |
| Vendor `trust_status ≠ VERIFIED` | 40 |

---

## 11. Decision engine (LangGraph)

### 11.1 Schemas (`backend/app/schemas/`)
```python
from typing import Annotated, Literal, Optional, TypedDict
import operator
from pydantic import BaseModel, Field

Cents = Annotated[int, Field(ge=0)]

class ShortPayAssumptions(BaseModel):
    p_recover: float = Field(0.60, ge=0, le=1)      # chance a pay-then-claim recovers Δ
    claim_ops_cost_cents: Cents = 3500              # staff time to pursue a claim
    annual_cost_of_capital: float = Field(0.10, ge=0, le=1)
    claim_days: int = Field(45, ge=0)
    notice_ops_cost_cents: Cents = 500              # cost to send a short-pay notice
    p_vendor_contests: float = Field(0.10, ge=0, le=1)
    contest_cost_cents: Cents = 10000               # escalation / relationship cost if contested

class MandatePolicy(BaseModel):
    policy_id: str
    version: int
    max_single_invoice_cents: Cents
    daily_cap_cents: Cents
    cap_timezone: str = "UTC"
    max_price_variance_bps: int = Field(0, ge=0, le=1000)
    auto_short_pay_enabled: bool = True
    max_short_pay_delta_cents: Cents = 10000
    dual_approval_risk_threshold: int = Field(80, ge=0, le=100)
    short_pay_assumptions: ShortPayAssumptions = ShortPayAssumptions()

class InvoiceExtraction(BaseModel):              # LLM output — evidence only (R3)
    vendor_invoice_number: str
    po_reference: Optional[str]
    payee_email: Optional[str]
    billed_qty: int = Field(ge=0)
    unit_price_cents: Cents
    total_cents: Cents
    currency: Literal["USD"]
    field_confidence: dict[str, float]

class HiddenSegment(BaseModel):
    page: int
    bbox: tuple[float, float, float, float]
    technique: Literal["COLOR_MATCH","TINY_FONT","OFF_PAGE","INVISIBLE_MODE","COVERED",
                       "ANNOTATION","METADATA","OCR_DIFF"]
    text: str

class ForensicsReport(BaseModel):
    detector_enabled: bool
    hidden_segments: list[HiddenSegment]
    classifier_label: Literal["CLEAN","SUSPICIOUS","INJECTION"]
    classifier_rationale: str
    detector_version: str

class MatchReport(BaseModel):
    po_id: str
    ordered_qty: int
    billed_qty: int
    accepted_qty: int
    po_unit_price_cents: Cents
    billed_unit_price_cents: Cents
    billed_cents: Cents
    accepted_value_cents: Cents                  # accepted_qty × PO unit price
    payee_matches_vendor_master: bool
    payee_lookalike: bool
    paypal_invoice_id: Optional[str]
    paypal_invoice_matches_pdf: Optional[bool]
    duplicate_of: Optional[str]
    fuzzy_duplicate_of: Optional[str]

class RiskContribution(BaseModel):
    finding: str
    points: int

class RiskAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    contributions: list[RiskContribution]

class EvidenceFact(BaseModel):
    claim: str
    tool_call_id: str                            # facts without a citation are dropped

class EvidencePack(BaseModel):
    summary: str
    facts: list[EvidenceFact]
    advisory_recommendation: Literal["APPROVE","SHORT_PAY","REJECT","NEEDS_INFO"]

class ShortPayDecision(BaseModel):
    delta_cents: Cents
    expected_cost_pay_and_claim_cents: Cents
    expected_cost_short_pay_cents: Cents
    chosen: Literal["SHORT_PAY","PAY_AND_CLAIM","NOT_APPLICABLE"]
    assumptions: ShortPayAssumptions

class HumanDecision(BaseModel):
    actor: str                                   # injected server-side from the session
    action: Literal["APPROVE","SHORT_PAY","REJECT"]
    reason: str = Field(min_length=10)

class GateDecision(BaseModel):
    route: Literal["AUTO_SETTLE","SHORT_PAY","FREEZE","DUPLICATE_BLOCKED","REJECTED"]
    payable_cents: Cents
    reasons: list[str]
    required_approvals: int                      # 0, 1, 2; 99 = not approvable
    allowed_actions: list[Literal["APPROVE","SHORT_PAY","REJECT"]]
    policy_id: str
    policy_version: int

class AegisState(TypedDict, total=False):
    execution_id: str
    document_id: str
    po_id: str
    vendor_id: str
    policy: MandatePolicy
    extraction: InvoiceExtraction
    forensics: ForensicsReport
    match: MatchReport
    risk: RiskAssessment
    evidence: Optional[EvidencePack]
    short_pay: Optional[ShortPayDecision]
    gate: GateDecision
    approvals: Annotated[list[HumanDecision], operator.add]
```

### 11.2 Policy gate (`decision/policy_gate.py`) — pure Python, table-driven
Evaluate **all** conditions, collect reasons, route by the most severe, and require the maximum approval level among the reasons.

| ID | Condition | Route | Approvals needed |
|---|---|---|---|
| G1 | `duplicate_of` set | `DUPLICATE_BLOCKED` | not approvable |
| G2 | today's committed + payable > `daily_cap_cents` | `FREEZE` (`OVER_DAILY_CAP`) | not approvable (change the mandate) |
| G3 | classifier `INJECTION`, payee mismatch, or vendor not `VERIFIED` | `FREEZE` (`HIGH_RISK`) | 2 if `risk ≥ dual_approval_risk_threshold`, else 1 |
| G4 | `billed_cents > max_single_invoice_cents` | `FREEZE` (`OVER_SINGLE_LIMIT`) | 1 |
| G5 | price variance > tolerance, or billed qty > ordered qty | `FREEZE` (`MISMATCH`) | 1 |
| G6 | no PayPal invoice found for this PDF | `FREEZE` (`UNVERIFIED_SOURCE`) | 1 |
| G7 | fuzzy duplicate | `FREEZE` (`POSSIBLE_DUPLICATE`) | 1 |
| G8 | accepted = billed qty and `billed_cents == accepted_value_cents` | `AUTO_SETTLE` | 0 |
| G9 | accepted < billed, short-pay enabled, Δ ≤ `max_short_pay_delta_cents`, model chose `SHORT_PAY` | `SHORT_PAY` | 0 |
| G10 | anything else | `FREEZE` (`NEEDS_REVIEW`) | 1 |

`payable_cents = min(billed_cents, accepted_value_cents)` for every paying route, including human approval (R3). A human `REJECT` → `REJECTED`. Approvals must come from distinct operators.

### 11.3 Graph topology (`decision/graph.py`)
```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

def build_graph(checkpointer: AsyncPostgresSaver):
    g = StateGraph(AegisState)
    g.add_node("ingest", ingest)                        # parse PDF + A2 extraction (no tools)
    g.add_node("forensics", forensics)                  # §10.1 detectors + A3 classifier (no tools)
    g.add_node("match", match)                          # PO ↔ PDF ↔ PayPal invoice ↔ GRN; duplicates
    g.add_node("score_risk", score_risk)                # §10.2, deterministic
    g.add_node("investigate", investigate)              # A4, read-only tools, only if risk ≥ 30
    g.add_node("short_pay_decide", short_pay_decide)    # §12, deterministic, no-op if nothing rejected
    g.add_node("policy_gate", policy_gate)              # §11.2
    g.add_node("human_review", human_review)            # interrupt()
    g.add_node("approve_for_payment", approve_for_payment)  # status + settlement_outbox row, one transaction
    g.add_node("close_rejected", close_rejected)
    g.add_node("close_duplicate", close_duplicate)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "forensics")
    g.add_edge("forensics", "match")
    g.add_edge("match", "score_risk")
    g.add_conditional_edges("score_risk", lambda s: s["risk"].score >= 30,
                            {True: "investigate", False: "short_pay_decide"})
    g.add_edge("investigate", "short_pay_decide")
    g.add_edge("short_pay_decide", "policy_gate")
    g.add_conditional_edges("policy_gate", lambda s: s["gate"].route, {
        "AUTO_SETTLE": "approve_for_payment",
        "SHORT_PAY": "approve_for_payment",
        "FREEZE": "human_review",
        "DUPLICATE_BLOCKED": "close_duplicate",
        "REJECTED": "close_rejected",
    })
    g.add_edge("human_review", "policy_gate")           # resume ALWAYS re-evaluates (R11)
    for n in ("approve_for_payment", "close_rejected", "close_duplicate"):
        g.add_edge(n, END)
    return g.compile(checkpointer=checkpointer)

def human_review(state: AegisState) -> dict:
    payload = interrupt({
        "execution_id": state["execution_id"],
        "reasons": state["gate"].reasons,
        "required_approvals": state["gate"].required_approvals,
        "allowed_actions": state["gate"].allowed_actions,
    })
    return {"approvals": [HumanDecision.model_validate(payload)]}

# Resume from POST /api/executions/{id}/decisions (actor from session):
# await graph.ainvoke(Command(resume=decision.model_dump()),
#                     config={"configurable": {"thread_id": execution_id}})
```
A dual-approval case loops `human_review → policy_gate → human_review` until two distinct operators approve or one rejects.

### 11.4 Settlement state machine (`settlement/`)
```text
APPROVED_FOR_PAYMENT ──outbox worker──► reserve spend (R8) ──► POST payout ──► PAYOUT_SUBMITTED
PAYOUT_SUBMITTED ──item SUCCESS (verified webhook or poll)──► PAYOUT_SUCCEEDED
PAYOUT_SUCCEEDED ──VendorGateway.record_payment──► SETTLED   (route SHORT_PAY → short_pay_balance = NOTICE_SENT)
PAYOUT_SUBMITTED ──FAILED/RETURNED/BLOCKED/DENIED/REFUNDED/REVERSED──► release ──► PAYOUT_FAILED
PAYOUT_SUBMITTED ──UNCLAIMED──► FROZEN(STALE_PAYEE) ──operator cancel──► release
```
```python
class SettlementService:
    async def execute(self, execution_id: str) -> None:       # the ONLY public entry point (R1)
        ex = await repo.get_execution_for_update(execution_id)
        assert ex.status == "APPROVED_FOR_PAYMENT"
        vendor = await repo.get_vendor(ex.vendor_id)          # payee source of truth
        await spend_ledger.reserve(ex)                        # raises OverCap → FROZEN
        resp = await payouts.create(
            sender_batch_id=f"AEGIS-{execution_id}",
            sender_item_id=f"{execution_id}-1",
            receiver=vendor.verified_paypal_email,
            amount_cents=ex.approved_cents,
            request_id=f"aegis-{execution_id}-payout",
        )
        await repo.record_payout(ex, resp)                    # batch_status PENDING
        await repo.transition(ex, "PAYOUT_SUBMITTED")
```
For the hackathon the outbox worker is an asyncio task in the API process, polling every 2 s with `SELECT … FOR UPDATE SKIP LOCKED`. Run a single API worker process.

---

## 12. Short-Pay Decision Model (replaces v1 "EV Short-Pay")

### 12.1 What it decides
The amount paid is fixed by R3 (`accepted_value`). The model decides **which action** to take on the rejected portion Δ: short-pay now and notify the supplier, or pay in full and claim Δ back later. All assumptions are mandate parameters, shown in the UI and labelled as assumptions.

### 12.2 Formulas
Let B = billed, A = accepted_qty × PO unit price, Δ = B − A.

- **Pay-and-claim:** E[cost] = (1 − p_recover)·Δ + claim_ops_cost + Δ·annual_cost_of_capital·(claim_days / 365)
- **Short-pay + notice:** E[cost] = notice_ops_cost + p_vendor_contests·contest_cost
- Choose `SHORT_PAY` when its expected cost is lower **and** Δ ≤ `max_short_pay_delta_cents`; otherwise `FREEZE`.

There is no buyer-side "dispute fee" in this model (v1's $15 fee does not apply to the buyer).

### 12.3 Scenario S2 worked example (default assumptions)
B = $500.00, A = 85 × $5.00 = $425.00, Δ = $75.00.

| Option | Calculation | Expected cost |
|---|---|---|
| Pay-and-claim | 0.40 × $75.00 + $35.00 + $75.00 × 0.10 × 45/365 | **$65.92** |
| Short-pay + notice | $5.00 + 0.10 × $100.00 | **$15.00** |

Decision: `SHORT_PAY`. Pay **$425.00** now; send a notice for the **$75.00** balance; **expected saving vs. pay-and-claim ≈ $50.92**. The UI must not claim "$125 saved".

### 12.4 Remedy for Δ (P0 notice; P1 agent-to-agent)
- **P0:** A5 drafts the notice; the vendor portal offers Accept / Contest. Accept → `short_pay_balance = ACCEPTED`. Contest → `CONTESTED`, surfaced to the controller.
- **P1 (agentic commerce):** the Vendor Agent (A6) replies with a structured `RemedyProposal ∈ {ACCEPT_WRITE_OFF, CREDIT_NOTE_NEXT_ORDER, REPLACEMENT_UNITS, CONTEST_WITH_REASON}`. The buyer side accepts the first three automatically and escalates `CONTEST_WITH_REASON` to a human with both agents' messages. **The payment amount never changes during negotiation** (R3) — agents negotiate the remedy for Δ, not the payout.

---

## 13. Backend API (`backend/app/routers/`)

All routes except `/api/health` and the PayPal webhook routes require an operator session (httpOnly cookie). Rate limit `demo/*` and `documents` (e.g., 10/min per session).

| Method | Path | Role | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Demo operator login (`controller`, `cfo`, `admin`) |
| `POST` | `/api/mandates/compile` | controller | NL → draft `MandatePolicy` (inactive) + diff vs active + clamped fields |
| `POST` | `/api/mandates/{policy_id}/versions/{v}/activate` | controller | Activate a draft; logs actor |
| `GET` | `/api/mandates/active` | any | Active policy + today's committed spend from `spend_ledger` |
| `POST` | `/api/documents` | any | Upload invoice PDF → creates execution, starts graph asynchronously, returns `execution_id` |
| `GET` | `/api/executions` | any | Ledger rows for the grid widget |
| `GET` | `/api/executions/{id}` | any | Full case: extraction, forensics, match, risk, evidence pack, short-pay, gate, approvals, payouts, PayPal IDs, webhook receipts |
| `GET` | `/api/executions/{id}/events?format=json\|csv` | any | Hash-chained audit export, with chain verification result |
| `POST` | `/api/executions/{id}/decisions` | controller, cfo | `{action, reason}`; actor from session; resumes the graph |
| `POST` | `/api/executions/{id}/payout/cancel` | controller | Cancel an `UNCLAIMED` payout item |
| `GET` | `/api/stream` | any | SSE: graph node transitions, settlement transitions, webhook receipts |
| `POST` | `/api/webhooks/paypal/treasury` | PayPal signature | Payout events |
| `POST` | `/api/webhooks/paypal/vendor` | PayPal signature | Invoicing events |
| `GET` | `/api/vendor-sim/invoices` | any | Supplier-side view of invoices, payments and notices |
| `POST` | `/api/vendor-sim/notices/{notice_id}/respond` | any | Accept / contest (P1: triggers the Vendor Agent) |
| `POST` | `/api/demo/reset` | admin | New run suffix; recreate PayPal invoices, PDFs, POs, GRNs; clear executions (keep webhook log) |
| `POST` | `/api/demo/scenarios/{scenario_id}/run` | any | Feed the seeded PDF for S1–S5 into `/api/documents` |
| `POST` | `/api/demo/forensics-detector` | admin | `{enabled: bool}` for the kill-switch proof |
| `GET` | `/api/health` | — | DB, both OAuth tokens, both webhook registrations, distinct accounts (R12), replay mode flag |

SSE fan-out: run one API worker process (in-process broadcast). If scaling beyond one process, switch to Postgres `LISTEN/NOTIFY`.

---

## 14. Frontend and AG Studio (`frontend/src/`)

### 14.1 Information architecture
1. **Cockpit** (AG Studio dashboard) — operational overview.
2. **Case view** `/cases/[id]` — evidence and decision for one invoice.
3. **Mandate studio** `/mandate` — compile, diff, activate.
4. **Vendor portal** `/vendor` — the supplier's side (clearly labelled "Simulator").
5. **Judge guide** — a dismissible panel explaining the four scenarios, operator logins, and the simulation boundary.

The demo loop must read as one story: **Inbox → Case → Decision → Settlement receipt → Supplier sees payment**.

### 14.2 AG Studio cockpit (primary sponsor target) [SPIKE-7]
Start from the official PayPal + AG Grid hackathon boilerplate. Custom widgets:
- **Payables ledger** — PO, vendor, triad pills (PO · Receipt · Source), billed → paid, risk meter, status; row click opens the case.
- **Mandate gauge** — today's committed vs daily cap, per active policy version.
- **Exposure by reason** — frozen dollars grouped by gate reason (G2–G10).
- **Decision path** — live node path for the selected execution via SSE (React Flow inside a custom widget if supported; otherwise a stepper).
- **Settlement timeline** — `SUBMITTED → SUCCEEDED → SETTLED`, with PayPal batch/item IDs and webhook receipts (verified badge; "simulated" tag when applicable).
- **Theme** — custom dark theme shared by Studio and the rest of the app.
- **P1 — Treasury Analyst agent** (Studio Agent Framework) on a read-only executions dataset: e.g., "show frozen invoices by reason this week" builds a widget.

Licence: never commit the key; inject `NEXT_PUBLIC_AG_LICENSE_KEY` at build on Render. Confirm the licence stays valid through 2026-12-15 (see risk register).

### 14.3 Case view
Three panes:
1. **Evidence** — side by side: PO · invoice PDF (rendered) · PayPal invoice · Goods receipt. Mismatched fields highlighted.
2. **Security** — PDF render with bounding-box overlays for each hidden segment (technique label on hover); classifier label and rationale; risk contributions table; payee panel stating **"Payee source: vendor master (locked)"** next to the extracted payee.
3. **Money** — gate reasons, short-pay model table (§12.3) with assumptions, approvals progress (e.g., "1 of 2"), payout and invoice IDs, settlement timeline, audit export button.

Actions (only when allowed by `GateDecision.allowed_actions`): **Approve** (pays `min(billed, accepted value)`), **Short-pay**, **Reject**. Each requires a reason.

### 14.4 Vendor portal (simulator)
Invoices issued by the supplier account, PayPal status per invoice, payments received, short-pay notices with Accept / Contest. P1: show the Vendor Agent's structured reply.

### 14.5 Demo toolbar (admin only)
Reset run · Run S1 / S2 / S3 / S4 / (S5) · Forensics detector ON/OFF · Replay-mode indicator.

---

## 15. Demo scenarios (`backend/app/demo/`)

Seeded for each run: vendor master (one verified supplier), POs, GRNs, PDFs generated with `reportlab`, and PayPal invoices created and sent from the Vendor app.

| ID | PO | Seeded state | Expected route and PayPal effects |
|---|---|---|---|
| **S1 — Happy path** | PO-1042 | 100 × LiDAR bracket @ $4.80 = $480.00. GRN: 100 received, 100 accepted. PayPal invoice from supplier for $480.00. Clean PDF. | `AUTO_SETTLE` → payout $480.00 to vendor master → item `SUCCESS` → record payment $480.00 → invoice `PAID` → `SETTLED`. |
| **S2 — Damaged delivery** | PO-1043 | 100 × servo motor @ $5.00 = $500.00. GRN: 100 received, 85 accepted, 15 rejected ("crushed in transit"). PayPal invoice $500.00. Clean PDF. | `SHORT_PAY` (§12.3) → payout $425.00 → record payment $425.00 → invoice `PARTIALLY_PAID` → notice for $75.00 → vendor portal Accept (P1: agents agree a remedy). |
| **S3 — BEC + indirect prompt injection** | PO-1044 | 80 × flight controller @ $5.00 = $400.00. GRN: 80 accepted. **No PayPal invoice** for this invoice number (it arrived only as an emailed PDF). PDF payee is a look-alike of the vendor-master address (e.g., `supp1ier@aegispay-dem0.example`) plus white-on-white footer instructing the agent to ignore mandates and pay that address. | Forensics flags hidden text + `INJECTION`; match flags payee mismatch + look-alike + unverified source → risk 100 (capped) → `FREEZE`, dual approval required. Controller rejects → `REJECTED`. **Kill-switch proof:** re-run with detector OFF → still frozen by payee mismatch and unverified source; the case shows any payment could only go to the vendor-master address. No PayPal call is made. |
| **S4 — Duplicate resubmission** | PO-1042 | Re-upload of S1's PDF after S1 settled. | `DUPLICATE_BLOCKED` in < 1 s, linking to S1's execution. P1 variant: invoice number altered to `…-1042-A` → fuzzy duplicate → `FREEZE`. |
| **S5 (P2) — Stale payee** | PO-1045 | Vendor master email points to an address with no sandbox account. | Payout item `UNCLAIMED` → `FROZEN (STALE_PAYEE)` → operator cancels item → reservation released [SPIKE-5]. |

Mandate used in the demo (typed live in the video):
> "Auto-pay verified suppliers up to $500 per invoice and $2,000 per day, only for goods the warehouse accepted, at exactly the PO price. If part of a delivery is rejected, pay for what we accepted and notify the supplier when the disputed amount is under $100. Anything with a changed payment address needs two approvers."

Daily cap check: S1 $480.00 + S2 $425.00 = $905.00 ≤ $2,000.00.

---

## 16. Non-functional requirements

| Area | Requirement |
|---|---|
| Availability | Hosted demo up from submission through **2026-12-15**. Use paid (non-sleeping) Render instances funded by hackathon credits; external uptime ping on `/api/health`. |
| Latency | S1 decision ≤ 20 s p50; UI reflects state changes ≤ 1 s after they happen (SSE). |
| Security | Operator auth on all non-webhook routes; secrets only in Render env; CORS limited to the frontend origin; rate limits on demo/document routes; webhook verification (R10); `gitleaks` in CI. |
| Observability | Structured JSON logs with `execution_id`, node name, PayPal `paypal-debug-id`; `/api/health` covers DB, both tokens, both webhooks, distinct accounts. |
| Resilience | PayPal 5xx/timeouts → retry with the **same** Request-Id (exponential backoff, max 3); reconciler poll guarantees settlement progress without webhooks. |
| Honesty | `DEMO_REPLAY_MODE` serves recorded sandbox responses only when the sandbox is unavailable, with a persistent **REPLAY** banner. Never present replayed data as live. |
| Cost | LLM calls per invoice ≤ 4 (A2, A3, optional A4, optional A5); log tokens per execution. |

---

## 17. Testing and evaluation

| Layer | What | Tooling |
|---|---|---|
| Unit | Policy gate truth table G1–G10, risk scoring, short-pay math (S2 numbers exact to the cent), hash chain | `pytest` |
| Property | For any extraction + any human input: receiver ∈ vendor master; paid ≤ accepted value; Σ reservations ≤ cap under concurrent executions | `hypothesis` |
| Static / architecture | Tool registry contains only read tools; `paypal/payouts.py` imported only from `settlement/`; no `float` in money schemas | `pytest` + `import-linter` + `ruff` |
| Contract | Every PayPal call against recorded sandbox fixtures; one manual live smoke job | `respx` |
| Webhook chaos | Duplicate, out-of-order, delayed, unsigned, simulator events | `pytest` |
| LLM eval | ≥ 12 labelled PDFs → injection precision/recall + extraction field accuracy, published in `docs/EVAL.md` | `pytest` eval suite |
| End-to-end | S1–S4 in replay mode (CI) and against sandbox (manual) | Playwright |

---

## 18. Day 1–2 verification spikes (do these before product code)

Record each result in this table and update the affected sections.

| ID | Question | How | Pass criterion | Fallback | Result |
|---|---|---|---|---|---|
| SPIKE-1 | Vendor app can create + send an invoice to the treasury email with `invoicer` = vendor account email | Two Business sandbox accounts, two REST apps | Invoice status `SENT`/`UNPAID` | — | 2026-10-22 PASS. Vendor app (country US, Invoicing feature enabled; token scope `https://uri.paypal.com/services/invoicing`) created a draft with `invoicer` = vendor account email and recipient = treasury email: HTTP 201 (debug-id f441602c35130). `/send` with `send_to_recipient=false`: HTTP 200 (f893447b8352a). GET: status `UNPAID`. Create without `invoicer` also 201 (defaults to the token's account). Findings: (1) India-country sandbox accounts blocked send (`INR_FOREIGN_CURRENCY_BLOCKED`) and INR currency is invalid; use US accounts, USD. (2) 403 `NOT_AUTHORIZED` on create until the app's Invoicing feature showed up in the token scopes; it cleared without any code change (dashboard change took time to apply, or needed a fresh save). Check the `invoicing` scope when debugging a 403. |
| SPIKE-2 | Can Treasury credentials read an invoice addressed to the treasury? | `GET` with Treasury token | 200 with invoice body | Read via VendorGateway (simulated consent, §3) | |
| SPIKE-3 | Payout Treasury → supplier: batch/item status transitions and webhook latency | Create payout; poll + webhook | Item reaches `SUCCESS`; timings recorded | Poll-only progress | |
| SPIKE-4 | Record payment (method `PAYPAL`) partial → `PARTIALLY_PAID`, full → `PAID`; which app receives which invoicing webhook | Vendor token | Statuses as expected; event names recorded | Track balance only in AegisPay | |
| SPIKE-5 | Payout to an email with no sandbox account → `UNCLAIMED`; cancel works | Payout + cancel | `UNCLAIMED` then cancelled | Drop S5 | |
| SPIKE-6 | PayPal Agent Toolkit (Python) works inside LangGraph with read-only configuration | Install, bind tools, run one read | Tool call returns invoice data; no write tools exposed | Own read tools with identical names; disclose in TOOLS.md | |
| SPIKE-7 | AG Studio: boilerplate runs; custom widget hosts React components; Studio Agent Framework hello-world; licence valid through 2026-12-15 | Boilerplate + docs + ask AG Grid team on Discord | All four confirmed | Studio widgets only; plain AG Grid ledger as last resort | |
| SPIKE-8 | Webhook self-verification for real events and simulator events (`WEBHOOK_ID`) | Recorded headers + raw body | Both verify; simulator flagged | Postback for real events only | |
| SPIKE-9 | `interrupt()` + `AsyncPostgresSaver` survives a process restart, then resumes | Kill and restart API mid-freeze | Resume succeeds | — | |

---

## 19. Delivery plan (dated, 2 developers)

Dev A = backend, PayPal, agents. Dev B = full stack (frontend + backend), AG Studio, video.

| Dates | Dev A | Dev B | Exit criterion |
|---|---|---|---|
| Oct 21–22 | SPIKE-1…6, 8, 9 | SPIKE-7; repo with LICENSE, CI, Render project + Postgres; claim Render credits and APIMatic subscription | Spike table filled; go/no-go on each assumption |
| Oct 23–27 | DB models, PayPal clients (both apps), SettlementService + outbox + reconciler, webhooks, seeder | App shell on AG Studio boilerplate; typed client from OpenAPI; mocked case view; 3 user conversations | **Headless S1:** CLI upload → real sandbox payout → invoice `PAID` |
| Oct 28 – Nov 2 | Extraction, forensics + eval set, match, risk, gate, HITL resume, short-pay model, duplicates, audit chain | Ledger, gauge, exposure widgets; case view panes; mandate studio; SSE | **Headless S1–S4 pass**; first `EVAL.md` numbers |
| Nov 3–6 | Investigator agent with Agent Toolkit; vendor sim + notices; auth; **deploy to Render by Nov 4** | Decision-path and settlement widgets; vendor portal; kill-switch; theme; Studio agent (P1) | **Hosted S1–S4 green on Render**; rough-cut video recorded Nov 5 as insurance |
| Nov 7–8 | P1 negotiation agent only if everything above is green; tests and hardening | Polish; Playwright; second video cut | Feature freeze Nov 8 EOD |
| Nov 9 | Bug fixes only; README, TOOLS.md, SANDBOX_SETUP.md, judge guide | Final recording and edit | Code freeze 23:59 IST |
| Nov 10 | Clean-clone verification from README; submission text | Upload to YouTube (public), captions | Full dry run of the submission form |
| Nov 11 | **Submit** (≥ 24 h before 2026-11-12 12:00 PT) | — | Submitted |

**Cut order if behind:** (1) P1 negotiation agent → (2) Studio Agent Framework agent (keep custom widgets) → (3) S5 → (4) React Flow → simple stepper → (5) OCR-diff detector (keep PDF-structure checks).
**Never cut:** payee lock, async settlement, duplicate check, operator auth, LICENSE + README + judge guide, the video.

---

## 20. Demo video storyboard (≤ 2:50, public YouTube, no copyrighted music)

| Time | Beat | On screen |
|---|---|---|
| 0:00–0:15 | Hook | IC3 BEC statistic; one line: agents that read invoices can be talked into paying the wrong person |
| 0:15–0:35 | Who and what | Persona + user quote; mandate typed → compiled → diff → activated |
| 0:35–1:00 | S1 | Upload → decision path lights up → payout → **PayPal sandbox account shows the payment** → invoice `PAID` with verified webhook |
| 1:00–1:30 | S2 | 85/100 accepted → short-pay table ($15.00 vs $65.92) → $425.00 paid → supplier sees notice (P1: agents agree a remedy) |
| 1:30–2:10 | S3 | Hidden text revealed with overlay → risk breakdown → dual approval required → reject. Detector OFF → still frozen; "Payee source: vendor master (locked)" |
| 2:10–2:22 | S4 | Duplicate blocked instantly |
| 2:22–2:40 | Cockpit | AG Studio widgets; (P1) analyst agent builds a widget from a question |
| 2:40–2:50 | Close | Decide/Execute diagram; PayPal APIs used; repo + live demo URL |

---

## 21. Submission checklist

- [ ] Public repo; `LICENSE` detected in GitHub's About panel.
- [ ] README: what it is, hosted URL, operator logins for judges, 4-scenario walkthrough, simulation boundary (§3), self-host steps linking `docs/SANDBOX_SETUP.md`.
- [ ] Hosted demo verified from a clean browser session; stays up through 2026-12-15.
- [ ] AG Studio licence valid through 2026-12-15.
- [ ] `docs/TOOLS.md`: LLM provider + model IDs and what each does; PayPal APIs used; Agent Toolkit usage; APIMatic Context Plugin usage during development; AG Studio usage; Render usage.
- [ ] `docs/EVAL.md` with real numbers.
- [ ] Video < 3 min, public on YouTube, shows the project working end-to-end, no third-party copyrighted music or trademarks without permission.
- [ ] Devpost text: problem, persona, how it works, what's real vs simulated, what was built during the submission period.
- [ ] Sponsor prize selection on the form: AG Grid.

---

## 22. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sandbox payouts or webhooks slow/unavailable while recording or judging | Medium | High | Record a rough cut by Nov 5; reconciler polling; labelled replay mode |
| AG Studio trial licence expires during judging (Dec 1–15) | Medium | High | SPIKE-7; ask the AG Grid team for a key valid through Dec 15; otherwise start the trial as late as feasible |
| Render instance sleeps or restarts during judging | Medium | High | Paid instances via credits; Postgres checkpointer (SPIKE-9); uptime ping |
| PayPal API assumption wrong | Medium | High | Day 1–2 spikes with documented fallbacks |
| LLM extraction errors | Medium | Medium | Extraction is evidence only (R3); confidence threshold → freeze; eval set |
| Scope overrun | High | High | Cut order (§19); weekly checkpoint against exit criteria |
| Secrets leaked in public repo | Low | High | `gitleaks` in CI; env-only secrets; rotate after judging |
| Judges cannot self-host (two PayPal apps) | Medium | Medium | Hosted demo as the primary path; step-by-step sandbox setup doc |
| "Agentic commerce" read as consumer shopping only | Medium | Medium | Frame as agents transacting for businesses; P1 agent-to-agent remedy negotiation |

---

## 23. Open questions

| ID | Question | Owner | Decide by |
|---|---|---|---|
| Q1 | Pay by `PAYPAL_ID` (payer ID, binds to the account) instead of `EMAIL`? Requires the supplier's payer ID in vendor master. | Dev A | After SPIKE-3 |
| Q2 | Should `HIGH_RISK` cases be approvable at all in the demo configuration, or reject-only? | Both | Oct 27 |
| Q3 | Which single LLM provider, and per-run cost cap? | Dev A | Oct 22 |
| Q4 | Keep the dark "terminal" aesthetic or adopt the AG Studio theme as the base? | Dev B | Oct 23 |
