# AegisPay — Zero-Trust Agentic Payables on PayPal
**Product Requirements Document (PRD) & Software Requirements Specification (SRS) — v2.1**

Revised: 2026-10-22 (v2.1 security review) · Submission deadline: **2026-11-12 12:00 PT (2026-11-13 01:30 IST)** · Planned submission: **2026-11-11** · Team: 2 developers · Judging period: 2026-12-01 → 2026-12-15

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

**v2 → v2.1 (2026-10-22, security review — "Swiss cheese" pass).** Four parallel reviews (trust inputs, settlement, AI, identity/ops) found holes that line up into payment paths. Every CRITICAL, HIGH and MEDIUM finding is closed by the sections below; none may ship open.

| # | Hole (v2) | Closed by |
|---|---|---|
| 17 | "Guarantees goods received / by architecture" overclaimed | §1.1 wording; §4.0 threat model + field provenance; R16 |
| 18 | Vendor master was the new single point of attack | R15 change control (dual approval, cooling period, SoD, immutable history); `vendor_master_changes` |
| 19 | Same PO/GRN payable repeatedly (no consumption ledger; invoice-number games) | R17 `po_ledger`; normalized invoice number; G11, G13 |
| 20 | Crash between payout POST and DB commit | R19 write-ahead `PAYOUT_SENDING`, leased outbox claims, adopt-existing-batch recovery |
| 21 | Idempotency keys fixed while amount/payee could change | R5: amount, receiver, vendor version frozen into the outbox command; response asserted |
| 22 | Role collapse; one person compiles, activates and approves; demo routes in prod | R18 roles + SoD; R24 `DEMO_ENABLED`; distinct human approvers |
| 23 | Audit chain re-hashable by a DB admin | R21 HMAC chain with off-DB key + external anchor |
| 24 | Gate trusted LLM-extracted payee/amount; PDF vs PayPal mismatch not a rule | R3 billed from structured source; G3/G6 extended |
| 25 | Payee not re-checked between approval and payout | R20 pre-payout recheck against approved vendor version |
| 26 | Resume payload unbound to what the human saw | R11 `decision_id` + `gate_hash` binding |
| 27 | Mandate compiler loosened unclamped fields | R13 bounds on every field; second-operator activation |
| 28 | Investigator output persuaded approvers | R14 extended to A4; quoted tool output; advisory hidden on G3 |
| 29 | Webhook forgery / regressions / double record-payment | R6 monotonic transitions; R10 anchored host, resource ownership, GET-confirm |
| 30 | One process + one `.env` held every secret beside PDF parsers | R22 service + secret split; sandboxed parser |
| 31 | UNCLAIMED release with broken cancel; no reversal path | R8 release only on confirmed non-payment; `NEEDS_MANUAL_REVIEW` |
| 32 | No velocity / new-vendor / kill switch / alerts | R23; G12 |
| 33 | PayPal invoice left payable → double settlement | R20 invoice must be open; record-payment watchdog |
| 34 | MEDIUM: risk gaming, A5 leakage, sessions/CSRF, precision, email-only payee, small eval set, replay rows, wording | G3/G14, R14, R25, R4, R15 + Q1, §10.1, R24, §1.1 |

---

## 1. Product definition

### 1.1 One-liner
AegisPay lets a small finance team hand vendor-invoice payment to an AI agent that **actually pays through PayPal**, while deterministic code outside the agent's reach — not a prompt — ensures money moves only to a **change-controlled, verified vendor record**, only against an **authenticated acceptance record from an approved source**, and only within a **mandate one human drafted and a second human activated**.

What this does **not** claim: protection against a compromised operator pair, database owner or host, or a goods receipt that an authorised receiver records falsely. Those are bounded by separation of duties, caps and audit (§4.0), not prevented.

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
| US-1 | As a controller, I write spending rules in plain English and get a structured, versioned policy that a second operator must explicitly activate. | Compiled values shown as a deterministic field-by-field diff against the active policy; every field is bounded by an org hard limit and clamped values are flagged; activator ≠ author (R13); activation logs actor + version; every execution stores the policy version it used. |
| US-2 | A fully matched invoice inside the mandate is paid through PayPal with no human touch. | Payee taken from the approved vendor-master version only; amount = `min(billed, PO unit price × accepted qty)` and within the PO's remaining accepted value (R17); UI shows `PAYOUT_SENDING → PAYOUT_SUBMITTED → PAYOUT_SUCCEEDED → SETTLED` with PayPal IDs. |
| US-3 | When part of a delivery is rejected, the agent pays for what was accepted and notifies the supplier. | Decision shows the compared options and their assumptions; supplier receives a notice with the numbers; PayPal invoice shows `PARTIALLY_PAID`. |
| US-4 | Risky invoices freeze with an evidence pack, and I approve, short-pay, or reject. | Reasons + risk breakdown shown; dual approval by two distinct humans (neither the uploader) when risk ≥ threshold and always for `HIGH_RISK`; an approval is void if the case changes after it was given (R11); a human can never change the payee or pay more than the PO value of accepted goods. |
| US-5 | A resubmitted invoice — or the same goods re-billed under a new number — is never paid twice. | S4 is blocked within one second with a link to the original execution; a PO is never paid beyond its accepted value (R17). |
| US-6 | I can export the full timeline of any payment and prove it was not rewritten. | Append-only, HMAC-chained event log with a published external anchor (R21); JSON and CSV export with verification against the anchor. |
| US-7 | As a supplier, I can see invoice status, payments, and short-pay notices. | Vendor portal (simulator) view with accept/contest actions. |
| US-8 (P1) | I can ask questions about payables in natural language and get a live widget. | AG Studio Studio Agent Framework agent builds a widget from a prompt. |

### 1.5 Success metrics (all demonstrable in the repo or video)

| Metric | Target | Evidence |
|---|---|---|
| Payouts to a receiver not in the approved vendor-master version | 0 in code (property-tested) **and** at runtime (receiver in every PayPal response asserted, R5) | `docs/EVAL.md` test report |
| Payments without policy version + evidence pack | 0 | Audit export |
| PO paid beyond accepted value | 0 (property-tested, R17) | `docs/EVAL.md` |
| Injection detection on eval set (n ≥ 50 PDFs, held-out split, 5 runs each) | Report "N of M per class" with Wilson 95% intervals; no single-number detection-rate claim | `docs/EVAL.md` |
| Field extraction accuracy (invoice no., qty, unit price, payee) | ≥ 95% field-level on the held-out split, with interval | `docs/EVAL.md` |
| Decision latency S1 (upload → `APPROVED_FOR_PAYMENT`) | ≤ 20 s p50 | UI timer |
| Settlement confirmation | Shown live; typical sandbox time measured in SPIKE-3 | UI timeline |
| Duplicate invoices paid | 0 | Scenario S4 |

### 1.6 Non-goals (v2)
Live (non-sandbox) PayPal (boot refuses live credentials while `DEMO_ENABLED` or `DEMO_REPLAY_MODE` is on, R24); multi-currency (USD enforced by CHECK); multi-line POs (schema leaves room, P2); real ERP/WMS or mailbox ingestion (upload + seeded inbox; POs imported by admin, GRNs recorded by an authenticated `receiver`); full KYC (vendor onboarding **is** modelled: verified PayPal identity + change control, R15); tax/VAT; non-PayPal payment rails.

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
| Presentation | Storyboarded video (§20) with live sandbox evidence and a "kill-switch" proof that the payee lock does not depend on the detector (it does not claim the detector is unnecessary) |

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

### 4.0 Threat model and field provenance (v2.1)

**In scope (must be prevented):** a malicious or BEC-compromised vendor; any content inside a PDF or vendor-written PayPal field (indirect prompt injection); a single malicious or careless operator; webhook forgery/replay; process crashes and PayPal retries; LLM error.
**Bounded, not prevented (caps, SoD, alerts, audit):** two colluding operators in different roles; a receiver who records a false GRN; the vendor's own PayPal account being taken over.
**Out of scope (stated in README):** a compromised host, database owner role, or secret store. R21's external anchor makes such tampering *detectable* after the fact.

Every field the gate or settlement reads has exactly one provenance. **Untrusted fields are evidence only: they can freeze a case, never pay or raise an amount.**

| Field | Source | Trust |
|---|---|---|
| Payee (`paypal_payer_id`, email) | `vendor_master` approved version (R15) | Trusted, change-controlled |
| PO qty, unit price, currency | `purchase_orders` (admin import, versioned) | Trusted |
| Accepted qty | `goods_receipts` recorded by an authenticated `receiver` session (R16) | Trusted, SoD-bound |
| Billed amount / qty / unit price | PayPal invoice from the vendor's account (structured JSON) | **Vendor-asserted** — upper bound only (R3) |
| Invoice number | PayPal invoice, else PDF; normalized (R9) | Vendor-asserted — dedupe key only |
| Everything in the PDF (payee, amounts, text) | A2 extraction | **Untrusted** — cross-check only |
| Investigator facts, notices, vendor-agent replies | LLM / vendor | **Untrusted** — display only, quoted |
| Webhook bodies | PayPal, signature-verified | Hint only; confirmed by `GET` (R10) |

| ID | Rule | Enforced by |
|---|---|---|
| **R1 — Payee lock** | The payout receiver is always the vendor-master version frozen into the outbox command at approval: `paypal_payer_id` (`recipient_type=PAYPAL_ID`, Q1 decided; SPIKE-10 PASS 2026-10-23, so the `verified_paypal_email` fallback is not used). No code path accepts a payee from an LLM, a PDF, a PayPal invoice, or a human request body. | `SettlementService.execute(execution_id)` takes **only** an execution ID; property test (§17) |
| **R2 — Privilege separation** | LLM-reachable components get read-only tools only. No PayPal write tool exists in any tool registry an LLM can see. The Treasury secret is not present in any process that runs an LLM or parses a PDF (R22). | Startup assertion over the tool registry; import-boundary test: `paypal/payouts.py` may be imported only from `settlement/`; boot assertion on env per service |
| **R3 — Amount derivation** | `payable = min(billed, accepted_qty × PO unit price, PO remaining accepted value)` in cents. `billed` comes from the structured PayPal invoice; if none exists the case freezes (G6) and the PDF amount is used only as an upper bound. A human approval can lower but never raise it. | `policy_gate.payable_cents()`; property test |
| **R4 — Money type** | Integer minor units (cents) in DB and Python; `Decimal` only when parsing/formatting PayPal strings like `"480.00"`. Parsing rejects non-integer quantities, sub-cent prices and non-USD currency (→ G14). Price variance is measured on total cents. | Type hints + `ruff` rule banning `float` in `schemas/` and `settlement/`; parser tests |
| **R5 — Idempotency + frozen command** | `PayPal-Request-Id = f"aegis-{execution_id}-{step}"`; `sender_batch_id = f"AEGIS-{execution_id}"`; `sender_item_id = f"{execution_id}-1"`. PayPal does **not** replay a payout POST on a reused Request-Id (SPIKE-11: HTTP 400, not the original 201), so the Request-Id is not the double-pay guard; the guard is PayPal's `sender_batch_id` uniqueness plus AegisPay's write-ahead row and the bounded recovery in R19. `approve_for_payment` writes amount, receiver and vendor-master version into the outbox row; these are **immutable** (DB trigger). Any later change to amount or payee = reject this execution and create a new one. The payout is asserted from `GET` batch/item (the create response carries no items, SPIKE-10): item `recipient_type`, receiver and amount equal the command, else `NEEDS_MANUAL_REVIEW` + halt (R23). Re-running a scenario creates a new `execution_id`. | `paypal/client.py` requires `request_id`; immutability trigger; response assertion test |
| **R6 — Async payouts, monotonic state** | Payout creation returns `PENDING`. Only a confirmed item `SUCCESS` (R10: webhook hint + `GET`, or poll) moves `PAYOUT_SUBMITTED → PAYOUT_SUCCEEDED`. Every transition goes through one `transition(ex, to, expected_version)` that checks an allowed-pairs table under row lock; illegal or backward transitions (e.g. late `PENDING` after `SUCCESS`) are logged and ignored. A reversal after `SUCCEEDED`/`SETTLED`, or a 30-min poll window ending non-terminal, goes to `NEEDS_MANUAL_REVIEW`. | `settlement/reconciler.py`; transition-table tests; chaos tests |
| **R7 — Vendor-side reconciliation** | `POST /v2/invoicing/invoices/{id}/payments` is called with **Vendor** credentials via `VendorGateway`, after confirmed payout success, with `PayPal-Request-Id = aegis-{execution_id}-recordpay`, exactly once (guarded by the transition). `PAID` and `MARKED_AS_PAID` both mean fully paid (SPIKE-4). | `vendor_sim/gateway.py` is the only caller |
| **R8 — Atomic spend cap** | Before a payout, reserve the amount in `spend_ledger` inside a transaction that locks the active mandate row (`SELECT … FOR UPDATE`). `business_date` = the reservation's date in `cap_timezone`; a release references its reserve entry and lands on the **same** `business_date`. Release only on confirmed non-payment (`FAILED`, `RETURNED`, `BLOCKED`, `DENIED`, or a confirmed cancel). `UNCLAIMED` stays reserved until a cancel succeeds. Caps are keyed by `policy_id` across versions. | `settlement/spend_ledger.py`; concurrency + release-date tests |
| **R9 — Duplicate prevention** | Invoice numbers are normalized (`upper`, strip whitespace and `-_/.`, strip leading zeros of numeric runs) before indexing. Unique `(vendor_id, invoice_number_norm)` and unique `paypal_invoice_id` among non-rejected executions; a missing invoice number freezes (G13); fuzzy check (same vendor + PO, amount within 10%, within 30 days, normalized edit distance ≤ 2) freezes. Re-billing the same goods is stopped by R17 regardless of number. | DB partial unique indexes + `match` node |
| **R10 — Webhook verification** | Verify every webhook from the **raw request bytes** (CRC32 + transmission headers + the receiving app's own webhook ID); fetch the cert only over HTTPS from an **exact** host allowlist (`api.paypal.com`, `api.sandbox.paypal.com`, `api-m.*` equivalents), validate its chain and expiry, cache it by URL. Reject events whose app does not match the route, whose `resource_id` is not a payout/invoice we created, or whose transmission time is > 10 min old. Body cap 64 KB. A verified webhook is a **hint**: money state changes only after a `GET` of the resource confirms it. Simulator events verify with `WEBHOOK_ID`, are stored with `simulated=true`, and **never** change money state. | `paypal/webhooks.py`; forgery tests [SPIKE-8] |
| **R11 — HITL integrity** | Decision endpoints require an operator session; the actor comes from the session, never the request body; a reason is mandatory. Each approval row stores the `gate_hash` (hash of route, payable cents, reasons, policy version, vendor-master version, PO/GRN versions) it was given against; resume passes only a `decision_id` and the graph loads the row from the DB. Resume re-runs `match → score_risk → policy_gate` on fresh DB data; if `gate_hash` changed, earlier approvals are void. Distinct-human approvals are counted **inside** `policy_gate`. The uploader of a document cannot approve it. Duplicates, over-cap and over-PO cases cannot be approved. | `routers/executions.py`, `decision/policy_gate.py`; replay/stale-approval tests |
| **R12 — Distinct sandbox accounts** | Treasury and Supplier are different sandbox accounts (prevents `CANNOT_PAY_SELF`). | `/api/health` check |
| **R13 — Mandate integrity** | LLM-compiled policy → Pydantic validation → clamp **every** field to an `ORG_HARD_*` bound (amounts, variance, short-pay delta, dual-approval threshold, allowed timezones, short-pay assumptions) → deterministic field-by-field diff (no LLM summary) → activation by an operator **other than** the author → immutable versioned row. Changing `cap_timezone` takes effect the next business day. | `routers/mandates.py`; DB CHECK `activated_by <> created_by`; clamp tests per field |
| **R14 — LLM isolation** | A2, A3, A5, A6 have **zero** tools; document and vendor text is passed as delimited data; outputs are schema-validated; numbers in any drafted message are filled by template. A4 (the only LLM with tools) gets vendor free-text fields wrapped and flagged as untrusted; its facts are shown as quoted tool output, not paraphrase. A5 receives a whitelist (`delta_cents`, `accepted_qty`, `rejected_qty`, `inspection_note`, invoice/PO numbers) — never gate reasons, thresholds or mandate values. A3 or A2 failure fails **closed** (`FREEZE`). | `forensics/`, `vendor_sim/notices.py`, `decision/nodes/investigate.py` |
| **R15 — Vendor-master change control** | Vendors are onboarded with a verified PayPal identity (`paypal_payer_id` captured from the vendor's own PayPal account, plus an onboarding record; API path: `payee.merchant_id` of an order the vendor app creates and never approves, SPIKE-10). Any payee change is a `vendor_master_changes` row: requested by a `vendor_admin`, approved by a **different** `vendor_admin` or `cfo`, effective only after `PAYEE_COOLING_HOURS` (default 72; demo seeds an already-elapsed change). During cooling, `trust_status = COOLING` and G3 freezes. Old and new values are kept forever (append-only). `vendor_admin` cannot approve invoices; invoice approvers cannot edit vendors; **no AI component has any write path to vendor master.** Each change raises an alert (R23). | `routers/vendors.py`; DB trigger; SoD tests |
| **R16 — Authenticated acceptance** | A GRN is valid only if recorded through `POST /api/grns` by an authenticated `receiver` session (or the admin seeder, tagged `source=SEEDED`). GRNs are append-only; corrections are a new version that supersedes the old and invalidates any approval bound to it. Rejected: `accepted_qty > ordered_qty − already accepted`, `received_at` in the future. `po.created_by ≠ grn.recorded_by ≠ approver`. Execute re-checks the GRN version is the one approved. | `routers/grns.py`; CHECKs + tests |
| **R17 — PO consumption** | `po_ledger` reserves `qty` and `cents` per PO atomically (row lock on the PO) at `approve_for_payment`, released only with the spend reservation. Invariant: Σ reserved cents ≤ Σ accepted qty × unit price for that PO. A PO auto-closes when fully paid. Over-consumption → G11, not approvable. | `settlement/po_ledger.py`; property test |
| **R18 — Roles and separation of duties** | Operators are named individuals in an `operators` table (argon2 password hash, one role each): `controller` (mandate author, approver), `cfo` (approver, mandate activator, halt), `receiver` (GRNs only), `vendor_admin` (vendor changes only), `admin` (demo + ops; **cannot** approve, activate or edit vendors). Judges get one login per role; the README never publishes a pair that alone satisfies dual approval for the same role. | `auth.py`; role-matrix tests |
| **R19 — Crash-safe settlement** | Write-ahead: in one transaction reserve spend + PO, insert the `payouts` row with keys, and move to `PAYOUT_SENDING`; commit; **then** POST. Outbox claims are leases (`claimed_until`, 60 s). On restart or retry, a `PAYOUT_SENDING` row without a `payout_batch_id` is recovered by re-POSTing with the **same** keys (PayPal has no lookup by `sender_batch_id`; a used one returns a duplicate-batch error that links the existing batch, SPIKE-11). Every recovery re-POST is preceded by the R20 recheck and a `SETTLEMENT_HALT` check (R23), and is allowed only within `PAYOUT_RECOVERY_TTL_MIN` (default 30) of entering `PAYOUT_SENDING`. A duplicate-batch error means "adopt the existing batch", never "failed". Halt, failed recheck, TTL passed, or any unresolved outcome → `NEEDS_MANUAL_REVIEW` with spend and PO reservations **kept** (R8, R17): money may already have moved. No DB lock is held across an HTTP call. | `settlement/service.py`; crash-injection tests at each step |
| **R20 — Pre-payout recheck** | Immediately before the POST, settlement verifies: `SETTLEMENT_HALT` is off; vendor-master version and `trust_status = VERIFIED` equal the approved snapshot; GRN and PO versions unchanged; the PayPal invoice is still open (`SENT`/`UNPAID`/`PARTIALLY_PAID`). Any failure → `FROZEN` with the reason, reservations released; on a `PAYOUT_SENDING` recovery (R19) → `NEEDS_MANUAL_REVIEW`, reservations kept. After `SETTLED`, if the vendor has not recorded payment within 15 min (production: the invoice is still payable), alert. | `settlement/service.py`; tests per check |
| **R21 — Tamper-evident audit** | One global chain, written under a Postgres advisory lock: `hash = HMAC-SHA256(AUDIT_HMAC_KEY, prev_hash ‖ canonical_json(row))`, key held only in the settlement service env. Every hour and at each `SETTLED`, the head hash is published outside the DB (a commit to a public `audit-anchors` git branch). Export verifies against the latest anchor. | `audit/events.py`, `audit/anchor.py`; tamper tests |
| **R22 — Process and secret isolation** | Three processes with disjoint env: `api` (routes, graph, LLM key, Vendor read creds, session secret), `settlement` worker (Treasury + Vendor write creds, audit HMAC key, no LLM, no PDF code), `parser` subprocess (no env, no network, CPU/memory/time limits, max 20 pages / 10 MB). DB roles: the `api` role cannot write `vendor_master`, `settlement_outbox` status, `payouts` or `spend_ledger`; only the `settlement` role can. | `render.yaml`; boot env assertions; DB grants test |
| **R23 — Halt, velocity, alerts** | `SETTLEMENT_HALT` (DB flag, `cfo`/`admin` toggle, checked every worker tick and in R20). Automatic halt on: > `VELOCITY_MAX_PAYOUTS_PER_HOUR` or > `VELOCITY_MAX_CENTS_PER_HOUR`, any R5 response mismatch, 3 `FAILED`/`RETURNED` in an hour, any audit verification failure. Alerts (log + webhook to a team channel) on: halt, vendor change, mandate activation, webhook signature failure, approval outside 07:00–21:00 org time. Logs exclude PII (vendor emails masked, no invoice text, no `raw_nl_prompt`). | `settlement/worker.py`, `ops/alerts.py` |
| **R24 — Demo isolation** | Demo routes, the detector toggle and replay exist only when `DEMO_ENABLED=true`; boot refuses `DEMO_ENABLED` or `DEMO_REPLAY_MODE` with a non-sandbox `PAYPAL_BASE_URL`. Replayed executions carry `replayed=true` (immutable column + audit event), never write to `spend_ledger`/`po_ledger`, and are excluded from caps and exports unless asked. Detector state is recorded on every execution. | `config.py` boot checks; tests |
| **R25 — Session and input hardening** | Session cookie `HttpOnly; Secure; SameSite=Strict`, 30-min idle / 8-h absolute TTL, rotated on login; CSRF token on every state-changing route; login throttled (5/min per IP + account, lockout 15 min); body caps (PDF 10 MB, JSON 64 KB); SSE requires a session and sends only execution IDs and states. Unverified webhooks keep a 2 KB sample, not the raw body. | `auth.py`, middleware; tests |

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

DECIDE and EXECUTE are **separate processes** (R22): `api` (FastAPI + LangGraph) and `settlement` (outbox worker + reconciler + webhook-confirmed transitions). They share only Postgres, through different DB roles. PDF parsing runs in a sandboxed `parser` subprocess with no environment. The audit head hash is anchored outside the DB (R21).

### 5.2 Architecture Decision Records (store each in `docs/adr/`)

**ADR-001 — Decide/Execute split.**
*Context:* LLM nodes read attacker-controlled documents. *Decision:* LangGraph decides; a deterministic `SettlementService`, in its own process with its own secrets (ADR-007), executes money movement from an outbox command frozen at approval (execution ID, amount, receiver, vendor-master version). *Positive:* injection cannot choose the payee or raise the amount even if every detector fails; settlement is retryable and testable. *Limit:* this protects the payee and amount, not whether the invoice is legitimate — that rests on R15–R17 and the gate. *Negative:* two state machines to keep consistent. *Alternatives:* tool-calling agent with "approval tools" (rejected — the agent could still construct payout arguments).

**ADR-002 — Postgres everywhere.**
*Context:* Render's filesystem is ephemeral; frozen threads must survive restarts during judging. *Decision:* Render Postgres for domain data and `AsyncPostgresSaver` checkpoints; SQLite only in unit tests. *Negative:* local setup needs Docker. *Alternatives:* SQLite on a persistent disk (rejected — weaker concurrency for R8).

**ADR-003 — Remove Catalog Products; demote Shipment Tracking to P2.**
*Context:* Catalog products carry no price; v1 tracking is deprecated, needs a prior capture, and is seller-asserted. *Decision:* the 3-way match is PO ↔ Invoice (PDF + PayPal invoice) ↔ Goods Receipt. Tracking returns only with the P2 deposit flow via Orders v2 `/track`. *Negative:* fewer PayPal endpoints on the slide. *Positive:* every PayPal call does real work.

**ADR-004 — One sponsor target: AG Studio.**
*Context:* Only one sponsor prize can be won. *Decision:* invest in AG Studio custom widgets, theming and a Studio Agent Framework agent; use Render and APIMatic without building for them. *Negative:* AG Studio learning curve and licence-term risk (SPIKE-7).

**ADR-005 — Dynamic interrupts + durable checkpointer.**
*Decision:* `interrupt()` inside `human_review` and `Command(resume=approval_id)` on decision; `human_review` always routes back through `match` to `policy_gate`, so the gate re-runs on fresh DB facts (v2.1, R11). Static `interrupt_before` is not used.

**ADR-006 — Integer cents.**
*Decision:* `BIGINT` cents in DB, `int` in Pydantic, `Decimal` only for PayPal string formatting.

**ADR-007 — Process and secret split (v2.1).**
*Context:* one process held both PayPal secrets, the LLM key and the session secret beside PDF parsers that read attacker files; one RCE paid anyone. *Decision:* `api`, `settlement` and `parser` processes with disjoint env and DB roles (R22); Render web service + background worker. *Negative:* SSE must use Postgres `LISTEN/NOTIFY` for settlement events; one more Render service. *Alternatives:* single process with import-boundary tests only (rejected — not a runtime boundary).

**ADR-008 — Trust roots are records, not documents (v2.1).**
*Decision:* payee, price and acceptance come only from change-controlled records (R15–R17); PDFs and vendor-written PayPal fields are evidence that can freeze, never pay. *Negative:* more tables and roles. *Positive:* closes the "aligned holes" found in the v2.1 review (§0 rows 17–34).

---

## 6. Repository structure (monorepo)

```text
aegispay/
├── LICENSE                         # MIT or Apache-2.0 — must show in GitHub's About panel
├── README.md                       # Judge quickstart, hosted URL, operator logins, simulation boundary, video link
├── render.yaml                     # api (web), settlement (worker), frontend (web), postgres — disjoint env (R22)
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
│   │   ├── auth.py                 # operator sessions (controller, cfo, receiver, vendor_admin, admin), CSRF (R18, R25)
│   │   ├── models/                 # ORM tables (§8)
│   │   ├── schemas/                # domain.py, state.py, api.py (§11)
│   │   ├── paypal/
│   │   │   ├── client.py           # httpx; OAuth token cache per app; Request-Id; logs paypal-debug-id
│   │   │   ├── payouts.py          # TREASURY app — importable only from settlement/
│   │   │   ├── invoicing.py        # VENDOR app — importable only from vendor_sim/ and read adapters
│   │   │   ├── webhooks.py         # self-verification + postback
│   │   │   └── toolkit.py          # PayPal Agent Toolkit, read-only configuration
│   │   ├── decision/               # graph.py, nodes/, policy_gate.py, risk.py, short_pay.py
│   │   ├── forensics/              # pdf_structure.py, ocr_diff.py, classifier.py, sandbox.py (parser subprocess, R22)
│   │   ├── settlement/             # main.py (worker entry), service.py, outbox_worker.py, reconciler.py,
│   │   │                           # transitions.py (R6), spend_ledger.py, po_ledger.py (R17), halt.py (R23)
│   │   ├── vendor_sim/             # gateway.py, notices.py, negotiator.py (P1)
│   │   ├── audit/                  # events.py (HMAC chain), anchor.py (R21), export.py
│   │   ├── ops/                    # alerts.py (R23)
│   │   ├── demo/                   # seeder.py, scenarios.py, pdf_factory.py, replay.py (only if DEMO_ENABLED)
│   │   └── routers/                # auth, mandates, documents, executions, vendors, grns, webhooks, vendor_sim,
│   │                               # demo (only if DEMO_ENABLED), ops (halt), health
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

### 6.1 Git workflow (decided 2026-10-22)
- Single shared repo, no forks. Trunk = `main`, protected: PR required, CI green (P0-03), 1 approval.
- Only checklist claims and docs-only edits are committed directly to `main`.
- Code: branch `<a|b>/<ID>-<slug>`, PR into `main`, squash-merge, conventional title citing the checklist ID.
- Other dev's review is mandatory for money-safety paths (R1–R4); otherwise author may self-merge on green CI.
- Never force-push; sync a branch with `git merge origin/main`.

---

## 7. Environment configuration (`.env.example`)

Each variable below is tagged with the **only** service that may receive it (R22): `[api]`, `[settlement]`, `[frontend]`. Boot fails if a service sees a variable not tagged for it.

```ini
# --- PayPal sandbox (shared) [api, settlement] ---
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com

# --- Treasury (buyer) REST app: Payouts [settlement only] ---
TREASURY_CLIENT_ID=
TREASURY_CLIENT_SECRET=
TREASURY_WEBHOOK_ID=                                      # [settlement, api: id only, used to verify]
TREASURY_ACCOUNT_EMAIL=treasury@aegispay-demo.example     # Business sandbox account behind this app

# --- Vendor Simulator REST app: Invoicing [settlement: write; api: read via VendorGateway read adapter] ---
VENDOR_CLIENT_ID=
VENDOR_CLIENT_SECRET=
VENDOR_WEBHOOK_ID=
VENDOR_ACCOUNT_EMAIL=supplier@aegispay-demo.example       # MUST equal invoicer.email_address

# --- LLM (Q3 decided 2026-10-26; exact IDs + pricing in docs/TOOLS.md) [api only] ---
LLM_PROVIDER=openai
LLM_API_KEY=
LLM_MODEL_EXTRACT=gpt-6.1-sol          # vision-capable
LLM_MODEL_REASON=gpt-6.1-sol

# --- Database (one DB, two roles, R22) ---
DATABASE_URL=postgresql+asyncpg://aegis_api:aegis@localhost:5432/aegis          # [api]
SETTLEMENT_DATABASE_URL=postgresql+asyncpg://aegis_settle:aegis@localhost:5432/aegis  # [settlement]
CHECKPOINT_DB_URL=postgresql://aegis_api:aegis@localhost:5432/aegis             # [api]

# --- Auth [api] — operators live in the `operators` table with argon2 hashes (R18); seeded from these once ---
SESSION_SECRET=
OPERATOR_SEED_FILE=                 # path to a git-ignored JSON of {username, role, password}; never commit

# --- Audit [settlement] ---
AUDIT_HMAC_KEY=                     # R21; never present in the api service
AUDIT_ANCHOR_REPO=                  # git remote + branch for head-hash anchors

# --- Org hard limits (every mandate field is clamped to these, R13) [api, settlement] ---
ORG_HARD_MAX_SINGLE_CENTS=100000
ORG_HARD_DAILY_CAP_CENTS=500000
ORG_HARD_VENDOR_DAILY_CAP_CENTS=150000
ORG_HARD_VENDOR_30D_CAP_CENTS=1000000
ORG_HARD_MAX_INVOICES_PER_VENDOR_PER_DAY=5
ORG_HARD_MAX_PRICE_VARIANCE_BPS=200
ORG_HARD_MAX_SHORT_PAY_DELTA_CENTS=20000
ORG_HARD_MAX_DUAL_APPROVAL_THRESHOLD=80
ORG_ALLOWED_TIMEZONES=UTC,America/Los_Angeles,Asia/Kolkata
PAYEE_COOLING_HOURS=72              # R15
VENDOR_NEW_DAYS=7                   # G12

# --- Velocity breaker + halt (R23) [settlement] ---
VELOCITY_MAX_PAYOUTS_PER_HOUR=20
VELOCITY_MAX_CENTS_PER_HOUR=300000
ALERT_WEBHOOK_URL=

# --- Demo controls (R24) [api] ---
DEMO_ENABLED=false                  # true only on the sandbox-hosted demo; boot refuses it with a live PAYPAL_BASE_URL
FORENSICS_DETECTOR_ENABLED=true     # toggle exists only when DEMO_ENABLED; state stored on every execution
DEMO_REPLAY_MODE=false              # requires DEMO_ENABLED; replayed rows tagged replayed=true, never touch ledgers

# --- Frontend ---
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_AG_LICENSE_KEY=         # set in Render env only; never commit
```

Secrets policy: `.env` is git-ignored; CI runs `gitleaks`; each Render service gets only its tagged variables; sandbox credentials are rotated after judging.

---

## 8. Data model (Postgres, SQLAlchemy 2.0 `DeclarativeBase`)

```sql
CREATE TABLE operators (                 -- R18: named people, one role each
    operator_id     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('controller','cfo','receiver','vendor_admin','admin')),
    password_hash   TEXT NOT NULL,           -- argon2id
    disabled        BOOLEAN NOT NULL DEFAULT FALSE,
    failed_logins   INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ
);

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
    created_by                   TEXT        NOT NULL REFERENCES operators(operator_id),
    activated_by                 TEXT        REFERENCES operators(operator_id),
    activated_at                 TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, version),
    CHECK (activated_by IS NULL OR activated_by <> created_by)   -- R13: author cannot activate
);
CREATE UNIQUE INDEX one_active_mandate ON mandate_policies (is_active) WHERE is_active;
-- every mandate column also has a CHECK against its ORG_HARD_* bound, generated from config at migration time (R13)


CREATE TABLE vendor_master (             -- current row = latest effective change (R15)
    vendor_id               TEXT PRIMARY KEY,
    vendor_name             TEXT NOT NULL,
    paypal_payer_id         TEXT NOT NULL UNIQUE,        -- Q1 decided: primary binding [SPIKE-10]
    verified_paypal_email   TEXT NOT NULL UNIQUE,        -- display + fallback only
    trust_status            TEXT NOT NULL DEFAULT 'VERIFIED'
                            CHECK (trust_status IN ('VERIFIED','COOLING','BLOCKED')),
    version                 INTEGER NOT NULL DEFAULT 1,  -- bumped by every effective change; frozen into approvals
    onboarded_at            TIMESTAMPTZ NOT NULL,
    onboarding_evidence     JSONB NOT NULL,              -- how the PayPal identity was verified (e.g. payer-id lookup)
    payee_last_changed_at   TIMESTAMPTZ
);
-- Only the settlement DB role and routers/vendors.py (via an effective change) may UPDATE vendor_master.

CREATE TABLE vendor_master_changes (     -- append-only history of payee changes (R15)
    change_id         TEXT PRIMARY KEY,
    vendor_id         TEXT NOT NULL REFERENCES vendor_master(vendor_id),
    old_values        JSONB NOT NULL,
    new_values        JSONB NOT NULL,
    requested_by      TEXT NOT NULL REFERENCES operators(operator_id),
    approved_by       TEXT REFERENCES operators(operator_id),
    approved_at       TIMESTAMPTZ,
    effective_at      TIMESTAMPTZ,       -- approved_at + PAYEE_COOLING_HOURS
    status            TEXT NOT NULL CHECK (status IN ('REQUESTED','COOLING','EFFECTIVE','REJECTED')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (approved_by IS NULL OR approved_by <> requested_by)
);
-- No UPDATE/DELETE grants except status transitions by the vendors router; old/new values immutable (trigger).

CREATE TABLE purchase_orders (           -- single line per PO in v2 (multi-line is P2); admin import only
    po_id             TEXT PRIMARY KEY,
    vendor_id         TEXT   NOT NULL REFERENCES vendor_master(vendor_id),
    sku_id            TEXT   NOT NULL,
    item_name         TEXT   NOT NULL,
    ordered_qty       INTEGER NOT NULL CHECK (ordered_qty > 0),
    unit_price_cents  BIGINT NOT NULL CHECK (unit_price_cents > 0),
    currency          TEXT   NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    status            TEXT   NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    version           INTEGER NOT NULL DEFAULT 1,
    created_by        TEXT   NOT NULL REFERENCES operators(operator_id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Price/qty edits create a new version (old kept in po_versions, same shape); approvals bind to the version.

CREATE TABLE goods_receipts (            -- the "receiving" leg; append-only (R16)
    grn_id            TEXT PRIMARY KEY,
    po_id             TEXT    NOT NULL REFERENCES purchase_orders(po_id),
    supersedes_grn_id TEXT    REFERENCES goods_receipts(grn_id),   -- corrections are new rows
    received_qty      INTEGER NOT NULL CHECK (received_qty >= 0),
    accepted_qty      INTEGER NOT NULL CHECK (accepted_qty >= 0),
    rejected_qty      INTEGER NOT NULL CHECK (rejected_qty >= 0),
    inspection_note   TEXT,
    recorded_by       TEXT    NOT NULL REFERENCES operators(operator_id),   -- role must be 'receiver' (or seeder)
    source            TEXT    NOT NULL CHECK (source IN ('RECEIVER_SESSION','SEEDED')),
    received_at       TIMESTAMPTZ NOT NULL CHECK (received_at <= now()),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (accepted_qty + rejected_qty = received_qty)
);
-- Trigger: Σ accepted_qty of non-superseded GRNs for a PO ≤ ordered_qty; recorded_by ≠ purchase_orders.created_by.

CREATE TABLE po_ledger (                 -- R17: PO consumption, mirrors spend_ledger
    entry_id        BIGSERIAL PRIMARY KEY,
    po_id           TEXT   NOT NULL REFERENCES purchase_orders(po_id),
    execution_id    TEXT   NOT NULL,
    kind            TEXT   NOT NULL CHECK (kind IN ('RESERVE','RELEASE')),
    qty             INTEGER NOT NULL CHECK (qty >= 0),
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    reserve_entry_id BIGINT REFERENCES po_ledger(entry_id),   -- set on RELEASE
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE invoice_documents (
    document_id         TEXT PRIMARY KEY,
    source              TEXT NOT NULL CHECK (source IN ('PDF_UPLOAD','SEEDED_INBOX')),
    sha256              TEXT NOT NULL,
    storage_path        TEXT NOT NULL,
    extraction_json     JSONB,             -- InvoiceExtraction (evidence only)
    forensics_json      JSONB,             -- ForensicsReport
    uploaded_by         TEXT NOT NULL REFERENCES operators(operator_id),   -- cannot approve this document (R11)
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE invoice_executions (
    execution_id           TEXT PRIMARY KEY,
    document_id            TEXT   NOT NULL REFERENCES invoice_documents(document_id),
    po_id                  TEXT   REFERENCES purchase_orders(po_id),
    vendor_id              TEXT   REFERENCES vendor_master(vendor_id),
    vendor_invoice_number  TEXT,                     -- as asserted
    invoice_number_norm    TEXT,                     -- R9 normalization; the dedupe key
    paypal_invoice_id      TEXT,
    langgraph_thread_id    TEXT   NOT NULL UNIQUE,
    policy_id              TEXT   NOT NULL,
    policy_version         INTEGER NOT NULL,
    billed_cents           BIGINT,                   -- from the PayPal invoice (R3)
    approved_cents         BIGINT,                   -- write-once at APPROVED_FOR_PAYMENT (trigger)
    vendor_version         INTEGER,                  -- vendor_master.version at approval (R20)
    po_version             INTEGER,
    grn_id                 TEXT REFERENCES goods_receipts(grn_id),
    detector_enabled       BOOLEAN NOT NULL,         -- R24
    replayed               BOOLEAN NOT NULL DEFAULT FALSE,   -- R24, immutable
    row_version            INTEGER NOT NULL DEFAULT 0,       -- optimistic lock for transitions (R6)
    route                  TEXT CHECK (route IN ('AUTO_SETTLE','SHORT_PAY','FREEZE','DUPLICATE_BLOCKED')),
    status                 TEXT NOT NULL CHECK (status IN (
                              'RECEIVED','ANALYZING','FROZEN','REJECTED','DUPLICATE_BLOCKED',
                              'APPROVED_FOR_PAYMENT','PAYOUT_SENDING','PAYOUT_SUBMITTED','PAYOUT_SUCCEEDED',
                              'PAYOUT_FAILED','SETTLED','NEEDS_MANUAL_REVIEW')),
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
-- R9: never two live executions for the same (normalized) vendor invoice
CREATE UNIQUE INDEX uq_live_vendor_invoice ON invoice_executions (vendor_id, invoice_number_norm)
    WHERE status NOT IN ('REJECTED','DUPLICATE_BLOCKED') AND invoice_number_norm IS NOT NULL;
CREATE UNIQUE INDEX uq_live_paypal_invoice ON invoice_executions (paypal_invoice_id)
    WHERE status NOT IN ('REJECTED','DUPLICATE_BLOCKED') AND paypal_invoice_id IS NOT NULL;
-- NULL invoice_number_norm never auto-settles (G13).

CREATE TABLE approvals (
    approval_id     TEXT PRIMARY KEY,        -- the decision_id passed to resume (R11)
    execution_id    TEXT NOT NULL REFERENCES invoice_executions(execution_id),
    actor           TEXT NOT NULL REFERENCES operators(operator_id),   -- from session, never from request body
    action          TEXT NOT NULL CHECK (action IN ('APPROVE','SHORT_PAY','REJECT')),
    reason          TEXT NOT NULL CHECK (length(reason) >= 10),
    gate_hash       TEXT NOT NULL,           -- what the human saw; approval void if it changes
    voided_at       TIMESTAMPTZ,
    resume_checkpoint_id TEXT NOT NULL,     -- server-read pending checkpoint, never supplied by browser
    resume_interrupt_id TEXT NOT NULL,      -- exact pending human_review interrupt (R11, §11.3)
    consumed_at     TIMESTAMPTZ,             -- set after durable graph acceptance receipt, never before ainvoke
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (execution_id, actor, gate_hash)  -- one vote per human per case state
);

CREATE TABLE spend_ledger (               -- R8: committed(date) = Σ RESERVE − Σ RELEASE on that business_date
    entry_id        BIGSERIAL PRIMARY KEY,
    execution_id    TEXT   NOT NULL REFERENCES invoice_executions(execution_id),
    policy_id       TEXT   NOT NULL,         -- version-independent
    vendor_id       TEXT   NOT NULL,         -- per-vendor caps (G12)
    business_date   DATE   NOT NULL,         -- reservation date in cap_timezone; RELEASE copies it
    kind            TEXT   NOT NULL CHECK (kind IN ('RESERVE','RELEASE')),
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    reserve_entry_id BIGINT REFERENCES spend_ledger(entry_id),   -- required on RELEASE
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'RELEASE') = (reserve_entry_id IS NOT NULL))
);

CREATE TABLE settlement_outbox (          -- the frozen command (R5); amount/receiver immutable (trigger)
    command_id        TEXT PRIMARY KEY,
    execution_id      TEXT NOT NULL UNIQUE REFERENCES invoice_executions(execution_id),
    amount_cents      BIGINT NOT NULL CHECK (amount_cents > 0),
    receiver_payer_id TEXT NOT NULL,
    vendor_version    INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('PENDING','CLAIMED','DONE','FAILED')),
    claimed_until     TIMESTAMPTZ,           -- lease (R19); expired CLAIMED rows are reclaimable
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payouts (                    -- inserted BEFORE the POST (R19)
    payout_id         TEXT PRIMARY KEY,
    execution_id      TEXT   NOT NULL UNIQUE REFERENCES invoice_executions(execution_id),
    sender_batch_id   TEXT   NOT NULL UNIQUE,
    payout_batch_id   TEXT   UNIQUE,
    payout_item_id    TEXT   UNIQUE,
    receiver          TEXT   NOT NULL,       -- copied from the outbox command
    amount_cents      BIGINT NOT NULL,
    paypal_receiver   TEXT,                  -- as returned by PayPal; must equal receiver (R5)
    paypal_amount_cents BIGINT,              -- as returned by PayPal; must equal amount_cents (R5)
    batch_status      TEXT,
    item_status       TEXT,
    last_polled_at    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE execution_events (           -- append-only, single global HMAC chain (R21)
    event_seq       BIGSERIAL PRIMARY KEY,
    execution_id    TEXT  NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT  NOT NULL,          -- system | llm:<model-id> | operator:<id> | paypal:webhook
    event_type      TEXT  NOT NULL,
    payload         JSONB NOT NULL,
    prev_hash       TEXT  NOT NULL,          -- hash of event_seq − 1 (global), written under an advisory lock
    hash            TEXT  NOT NULL           -- HMAC-SHA256(AUDIT_HMAC_KEY, prev_hash || canonical_json(row without hash))
);
-- Only settlement holds the HMAC key and the INSERT grant on execution_events (no UPDATE/DELETE for anyone).
-- api appends unsigned rows to audit_queue (INSERT only); settlement drains it in order, signs and appends.
CREATE TABLE audit_queue (
    queue_seq       BIGSERIAL PRIMARY KEY,
    execution_id    TEXT  NOT NULL,
    actor           TEXT  NOT NULL,
    event_type      TEXT  NOT NULL,
    payload         JSONB NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_anchors (              -- R21: head hashes published outside the DB
    anchor_id       BIGSERIAL PRIMARY KEY,
    event_seq       BIGINT NOT NULL,
    head_hash       TEXT   NOT NULL,
    external_ref    TEXT   NOT NULL,         -- git commit SHA on the anchors branch
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE settlement_controls (        -- R23: single-row halt flag
    id              BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    halted          BOOLEAN NOT NULL DEFAULT FALSE,
    halted_reason   TEXT,
    changed_by      TEXT,
    changed_at      TIMESTAMPTZ
);

CREATE TABLE webhook_events (
    event_id            TEXT PRIMARY KEY,    -- PayPal event id → natural dedupe
    app                 TEXT NOT NULL CHECK (app IN ('TREASURY','VENDOR')),
    event_type          TEXT NOT NULL,
    resource_id         TEXT NOT NULL,
    signature_verified  BOOLEAN NOT NULL,
    simulated           BOOLEAN NOT NULL DEFAULT FALSE,
    headers_json        JSONB NOT NULL,
    raw_body            BYTEA,               -- full body only when verified; else ≤ 2 KB sample (R25)
    confirmed_by_get    BOOLEAN NOT NULL DEFAULT FALSE,   -- R10
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ
);
```

**Allowed execution transitions (R6)** — anything else is rejected and logged:
`RECEIVED→ANALYZING`; `ANALYZING→FROZEN|DUPLICATE_BLOCKED|APPROVED_FOR_PAYMENT`; `FROZEN→APPROVED_FOR_PAYMENT|REJECTED|FROZEN`; `APPROVED_FOR_PAYMENT→PAYOUT_SENDING|FROZEN`; `PAYOUT_SENDING→PAYOUT_SUBMITTED|PAYOUT_FAILED|NEEDS_MANUAL_REVIEW`; `PAYOUT_SUBMITTED→PAYOUT_SUCCEEDED|PAYOUT_FAILED|FROZEN(STALE_PAYEE)|NEEDS_MANUAL_REVIEW`; `PAYOUT_SUCCEEDED→SETTLED|NEEDS_MANUAL_REVIEW`; `SETTLED→NEEDS_MANUAL_REVIEW` (late reversal).

Removed from v1: `daily_spent_usd` (derived from `spend_ledger`), `paypal_capture_tx_id` / `tracking_number` / `carrier` on POs (ADR-003), `warehouse_*` columns on POs (now `goods_receipts`), the `WEBHOOK_VERIFIED` status (verification is a property of webhook events, not an execution status), and the duplicate price source on `vendor_master`.

---

## 9. PayPal sandbox API contracts

All calls go to `PAYPAL_BASE_URL`. Every POST carries `PayPal-Request-Id` (R5). Every response's `paypal-debug-id` header is logged with the `execution_id`.

### 9.1 OAuth (one token cache per app)
`POST /v1/oauth2/token` with HTTP Basic (`<APP>_CLIENT_ID`, `<APP>_CLIENT_SECRET`), body `grant_type=client_credentials`. Cache per app **per process**; refresh when `now >= expires_at - 300 s`. The Treasury token exists only in the `settlement` process (R22). Tokens and secrets are never logged.

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

**Read** — `GET /v2/invoicing/invoices/{invoice_id}`; list/search for the investigator's read tools. SPIKE-2: Treasury token gets 403, so reads go through the VendorGateway read adapter (§3). The `match` node takes `billed_cents`, qty, unit price, PO reference (`detail.reference`) and invoicer from this structured response (R3) and compares each with the PDF extraction; any disagreement sets `paypal_invoice_matches_pdf=false` (G6). Settlement re-reads status before paying (R20): only `SENT`, `UNPAID` or `PARTIALLY_PAID` may be paid.

**Record payment (after confirmed payout success only, R6/R7; `PayPal-Request-Id: aegis-{execution_id}-recordpay`)** — `POST /v2/invoicing/invoices/{invoice_id}/payments`
```json
{
  "method": "PAYPAL",
  "payment_date": "2026-11-05",
  "amount": { "currency_code": "USD", "value": "425.00" },
  "note": "AegisPay payout batch <payout_batch_id> item <payout_item_id>; short-pay notice <notice_id>"
}
```
Full amount → `MARKED_AS_PAID` (SPIKE-4; `PAID` when paid natively — treat both as fully paid); accepted-value amount on a short-pay → `PARTIALLY_PAID` with the balance tracked in `short_pay_balance`. Which app receives `INVOICING.INVOICE.PAID` is still open (P0-14). An over-recording error is mapped to `NEEDS_MANUAL_REVIEW`, never to payout failure — the money has already moved.

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
      "recipient_type": "PAYPAL_ID",
      "receiver": "<settlement_outbox.receiver_payer_id>",
      "amount": { "value": "480.00", "currency": "USD" },
      "sender_item_id": "<execution_id>-1",
      "note": "INV-2026-1042-<run> / PO-1042 / policy v<version>"
    }
  ]
}
```
`recipient_type=PAYPAL_ID` with the vendor's payer ID is [SPIKE-10]; SPIKE-10 passed (item `SUCCESS`), so no `EMAIL` fallback. Response is asynchronous: HTTP 201 with only `batch_header` (`PENDING`) and `links`, no items → execution `PAYOUT_SUBMITTED` (from `PAYOUT_SENDING`, R19). Item `recipient_type`, receiver and amount are asserted against the outbox command (R5) from the first `GET /v1/payments/payouts/{payout_batch_id}`, which echoes all three.

SPIKE-10 probe review (2026-10-26): PASS also requires successful batch and item GETs with item `SUCCESS` and exact recipient type, receiver and USD amount; a failed lookup, missing item ID or mismatch fails the probe. Historical sandbox evidence and offline regression limits are in [SPIKE-10](spikes/SPIKE-10.md). These probe checks do not implement the settlement controls above.

**Crash recovery (R19)** — a `PAYOUT_SENDING` execution without a `payout_batch_id` is resolved by re-POSTing with the **same** keys (SPIKE-11: there is no list/search by `sender_batch_id`, Transaction Search is not enabled on the Treasury app, and a same-`PayPal-Request-Id` retry is **not** replayed as the original 201). Each recovery attempt:
1. Within `PAYOUT_RECOVERY_TTL_MIN` of entering `PAYOUT_SENDING`, `SETTLEMENT_HALT` off, R20 recheck passes; else `NEEDS_MANUAL_REVIEW`.
2. Re-POST with the same keys. HTTP 201 → the first POST never landed; continue as a normal create.
3. HTTP 400 `USER_BUSINESS_ERROR` with a `details[]` entry `field = SENDER_BATCH_ID`, issue "Batch with given sender_batch_id already exists" → parse `payout_batch_id` from that entry's `link[].href` with a strict regex (`/v1/payments/payouts/([A-Z0-9]+)$`). Never follow the href: `GET /v1/payments/payouts/{payout_batch_id}` on the configured `PAYPAL_BASE_URL`, so the token never goes to another host.
4. Adopt only if the batch's `sender_batch_id` = `AEGIS-{execution_id}`, it has exactly one item, its `sender_item_id` = `{execution_id}-1`, and `recipient_type`, receiver, amount and currency equal the command (R5). Adopt writes `payout_batch_id` and `payout_item_id` in the same transaction as `PAYOUT_SUBMITTED`. A mismatch → persist the found `payout_batch_id`, `NEEDS_MANUAL_REVIEW` + halt (R23).
5. 5xx or timeout → retry with the same keys, at most 3 attempts with backoff, still inside the TTL; then `NEEDS_MANUAL_REVIEW`. Any other error, or no parsable link → `NEEDS_MANUAL_REVIEW`.

Every `NEEDS_MANUAL_REVIEW` exit from `PAYOUT_SENDING` keeps the spend and PO reservations (R8, R17).

**Status** — `GET /v1/payments/payouts/{payout_batch_id}` and `GET /v1/payments/payouts-item/{payout_item_id}`. The reconciler polls every 15 s for up to 30 min as a fallback to webhooks; SPIKE-3 measured item `SUCCESS` at ~18 s, so key on **item** status. A non-terminal item after 30 min → `NEEDS_MANUAL_REVIEW` (reservation kept). The reconciler holds a Postgres advisory lock so a deploy overlap cannot run two (R22).

**Item status handling** (via the R6 transition table):

| Item status | Action |
|---|---|
| `SUCCESS` | `PAYOUT_SUCCEEDED` → VendorGateway record payment → `SETTLED` |
| `PENDING`, `ONHOLD` | Keep polling; show in UI |
| `UNCLAIMED` | `FROZEN (STALE_PAYEE)`; **reservation kept** (the payee can still claim). Operator may `POST /v1/payments/payouts-item/{id}/cancel`; release only after a `GET` shows the item cancelled/returned. SPIKE-5: cancel returns 500 in the sandbox, so S5 shows the frozen state only |
| `FAILED`, `RETURNED`, `BLOCKED`, `DENIED` | `PAYOUT_FAILED`; release reservation (same `business_date`); surface "retry after vendor-master review" (a retry is a new execution) |
| `REFUNDED`, `REVERSED` (money came back after `SUCCESS`) | `NEEDS_MANUAL_REVIEW`; release reservation; alert; the vendor-side invoice payment must be reversed manually (listed in the case) |

### 9.4 Webhooks v1
- Two subscriptions: Treasury app → `PAYMENT.PAYOUTSBATCH.*`, `PAYMENT.PAYOUTS-ITEM.*`; Vendor app → `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.UPDATED`, `INVOICING.INVOICE.CANCELLED`. Confirm exact names in the dashboard during SPIKE-4.
- Endpoints: `/api/webhooks/paypal/treasury`, `/api/webhooks/paypal/vendor`.
- Verification per R10. Self-verification message: `<transmission_id>|<transmission_time>|<webhook_id>|<crc32(raw_body) as unsigned decimal>`, signature algorithm from `paypal-auth-algo` (allowlist `SHA256withRSA`), certificate from `paypal-cert-url` (exact-host allowlist, HTTPS, chain + expiry validated) [SPIKE-8].
- Order: size cap → parse headers → verify → dedupe by `event_id` → check `resource_id` is ours and the app matches the route → store → enqueue for settlement, which `GET`s the resource before any transition. Unverified events are stored as a 2 KB sample and alerted (R23).
- Local development needs a public HTTPS tunnel for the listener URL.
- Webhooks are an accelerator, not a dependency or a source of truth: the reconciler poll (§9.3) guarantees progress and every money transition is confirmed by `GET`.

### 9.5 PayPal Agent Toolkit — read-only (`paypal/toolkit.py`) [SPIKE-6]
- Instantiate one toolkit per credential set with **read actions only** (invoices: get/list; SPIKE-6: no `list_transactions` in the toolkit, so it is our own read tool, disclosed in TOOLS.md). Config is an explicit allowlist (`is_tool_allowed` passes if any action of a tool is enabled).
- Tool results from vendor-writable fields (invoice `note`, `memo`, `terms`, item descriptions) are returned wrapped as `<untrusted vendor_text>…</untrusted>` (R14).
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
| A1 | Mandate Compiler | Structured-output LLM | Controller's NL rules (typed by the controller, never pasted from a document) | `MandatePolicy` draft | None | Pydantic validation; clamp **every** field to `ORG_HARD_*`; deterministic diff; second-operator activation (R13) | Show validation errors; nothing activates |
| A2 | Invoice Extractor | Vision + text LLM | Rendered pages + text layer | `InvoiceExtraction` | None | Untrusted, cross-check only (§4.0); field confidence; delimited untrusted input (R14); a missing payee counts as payee mismatch | `FREEZE` reason `EXTRACTION_LOW_CONFIDENCE` |
| A3 | Injection Classifier | LLM + deterministic detectors | Hidden-text segments + full text as data | `ForensicsReport` | None | Never sees tools; detector results are independent of the LLM; `SUSPICIOUS` freezes (G3) | **Fail closed:** `classifier_label = INJECTION`, reason `CLASSIFIER_UNAVAILABLE` |
| A4 | Investigator | ReAct agent, ≤ 6 tool calls | Match report, risk breakdown | `EvidencePack` | Read-only Agent Toolkit + internal read tools (vendor history, PO, prior executions) | Every fact cites a tool-call ID **and** carries the verbatim tool-output excerpt it rests on; facts from vendor-writable fields are tagged `vendor_asserted`; recommendation is advisory, ignored by the gate and **not shown** on `HIGH_RISK` cases (R14) | Case shows "investigation unavailable" |
| A5 | Notice Drafter | LLM | Whitelisted fields only (R14): `delta_cents`, accepted/rejected qty, inspection note, invoice/PO numbers | Notice text with `{placeholders}` | None | Numbers inserted by template after generation; output scanned for any number or threshold not in the whitelist → fallback template | Fallback static template |
| A6 (P1) | Vendor Agent (simulator side) | LLM | Short-pay notice | `RemedyProposal` | None | Can only choose among structured remedies (§12.4); its free text is rendered to the buyer as inert quoted data | Escalate to human |
| A7 (P1) | Treasury Analyst | AG Studio Studio Agent Framework | NL question | Widget spec | Read-only executions dataset | No write access | Show error toast |

### 10.1 Forensics: deterministic hidden-text detection (`forensics/pdf_structure.py`, `ocr_diff.py`)
Detect and report each finding with page number and bounding box (for the UI overlay):
- text whose fill colour is within ΔE < 3 of the background beneath it (white-on-white);
- font size < 2 pt;
- text outside the page's crop box;
- invisible render mode, or text covered by an opaque shape;
- content in annotations, form fields, metadata/XMP, embedded files or JavaScript;
- **OCR-vs-text-layer diff:** render each page (e.g., 200 DPI), OCR it, and flag text-layer tokens that do not appear in the OCR output.

Libraries: `pdfplumber` (MIT) and `pypdfium2` (Apache-2.0/BSD) for parsing and rendering; Tesseract or the vision model for OCR. Avoid AGPL-licensed PDF libraries unless the repo itself is AGPL. All parsing and OCR run in the `parser` subprocess: empty env, no network, 20 pages / 10 MB / 30 s / 512 MB limits (R22).

**Eval set (`tests/fixtures/eval/`, ≥ 50 PDFs, ≥ 4 per class, 70/30 dev/held-out split; tune only on dev):** clean invoices; white-on-white; 1-pt text; off-page text; annotation payload; metadata payload; look-alike payee only (no injection); **adaptive attacks** — visible-text injection, multilingual, image-embedded text, instruction split across fields, homoglyph payee, payee omitted; OCR-adversarial renders; vendor-memo injection aimed at A4 (PayPal invoice `note`); and **false-positive traps** (legitimate footers such as "ignore this notice if already paid"). Run each LLM case 5 times. Publish per-class "N of M" with Wilson 95% intervals, run-to-run variance and field-level extraction accuracy in `docs/EVAL.md`; the video and README quote these counts, not a bare rate.

### 10.2 Deterministic risk score (`decision/risk.py`)
Additive, capped at 100. The score sets **only** the investigation trigger and the approval count; every safety-relevant finding also has its own gate rule (§11.2), so staying under a threshold never avoids a freeze. Contributions are shown to operators only (never in vendor-facing views or notices, R14).

| Finding | Points |
|---|---|
| Classifier = `INJECTION` (incl. classifier unavailable) | 60 |
| Classifier = `SUSPICIOUS` | 25 |
| Any hidden-text segment present | 25 |
| Extracted payee ≠ vendor master, or no payee extracted | 40 |
| Extracted payee is a look-alike of vendor master (edit distance ≤ 2 or homoglyph) | +10 |
| No PayPal invoice from the vendor's account for this invoice number | 30 |
| PayPal invoice ≠ PDF (amount, qty, unit price, PO ref or invoicer) | 40 |
| Unit price variance above tolerance | 20 |
| Billed qty > ordered qty, or PO over-consumption | 30 |
| Fuzzy duplicate | 30 |
| Vendor `trust_status ≠ VERIFIED` (incl. `COOLING`) | 40 |
| Vendor onboarded < `VENDOR_NEW_DAYS` ago, or per-vendor velocity exceeded | 20 |

---

## 11. Decision engine (LangGraph)

### 11.1 Schemas (`backend/app/schemas/`)
```python
from typing import Annotated, Literal, Optional, TypedDict
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

class MandatePolicy(BaseModel):                  # every field is clamped to an ORG_HARD_* bound after validation (R13)
    policy_id: str
    version: int
    max_single_invoice_cents: Cents               # ≤ ORG_HARD_MAX_SINGLE_CENTS
    daily_cap_cents: Cents                        # ≤ ORG_HARD_DAILY_CAP_CENTS
    vendor_daily_cap_cents: Cents                 # ≤ ORG_HARD_VENDOR_DAILY_CAP_CENTS
    cap_timezone: str = "UTC"                     # ∈ ORG_ALLOWED_TIMEZONES; change applies next business day
    max_price_variance_bps: int = Field(0, ge=0)  # ≤ ORG_HARD_MAX_PRICE_VARIANCE_BPS; on total cents
    auto_short_pay_enabled: bool = True
    max_short_pay_delta_cents: Cents = 10000      # ≤ ORG_HARD_MAX_SHORT_PAY_DELTA_CENTS
    dual_approval_risk_threshold: int = Field(80, ge=0)   # ≤ ORG_HARD_MAX_DUAL_APPROVAL_THRESHOLD
    short_pay_assumptions: ShortPayAssumptions = ShortPayAssumptions()   # each field has an org min/max

class InvoiceExtraction(BaseModel):              # LLM output — untrusted, cross-check only (§4.0)
    vendor_invoice_number: Optional[str]          # None → G13
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

class MatchReport(BaseModel):                    # built deterministically from DB rows + the PayPal invoice
    po_id: str
    po_version: int
    grn_id: str
    vendor_version: int
    ordered_qty: int
    billed_qty: int                              # PayPal invoice (PDF if none; then G6 fires)
    accepted_qty: int                            # GRN (R16)
    po_unit_price_cents: Cents
    billed_unit_price_cents: Cents
    billed_cents: Cents
    accepted_value_cents: Cents                  # accepted_qty × PO unit price
    po_remaining_cents: Cents                    # accepted value − Σ po_ledger reserved (R17)
    payee_matches_vendor_master: bool            # False if no payee was extracted
    payee_lookalike: bool
    paypal_invoice_id: Optional[str]
    paypal_invoice_status: Optional[str]
    paypal_invoice_matches_pdf: bool             # False when no PayPal invoice exists
    mismatched_fields: list[str]
    precision_ok: bool                           # integer qty, whole-cent prices, USD (R4)
    vendor_age_days: int
    vendor_invoices_today: int
    vendor_committed_today_cents: Cents
    invoice_number_norm: Optional[str]
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
    excerpt: str                                 # verbatim tool output; validator checks it occurs in that result
    vendor_asserted: bool                        # True if the excerpt comes from a vendor-writable field

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

class HumanDecision(BaseModel):                  # loaded from the approvals row, never built from a request (R11)
    approval_id: str
    actor: str
    action: Literal["APPROVE","SHORT_PAY","REJECT"]
    reason: str = Field(min_length=10)
    gate_hash: str

class GateDecision(BaseModel):
    route: Literal["AUTO_SETTLE","SHORT_PAY","FREEZE","DUPLICATE_BLOCKED","REJECTED"]
    payable_cents: Cents
    reasons: list[str]
    required_approvals: int                      # 0, 1, 2; 99 = not approvable
    allowed_actions: list[Literal["APPROVE","SHORT_PAY","REJECT"]]
    policy_id: str
    policy_version: int
    gate_hash: str                               # sha256 of route, payable, reasons, policy/vendor/PO/GRN versions

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
    approval_ids: list[str]                      # replaced, not appended; policy_gate reloads rows from the DB
    resume_receipts: dict[str, dict[str, str]]     # retained acceptance bindings keyed by approval_id (§11.3)
```

### 11.2 Policy gate (`decision/policy_gate.py`) — pure Python, table-driven
Evaluate **all** conditions, collect reasons, route by the most severe, and require the maximum approval level among the reasons.

| ID | Condition | Route | Approvals needed |
|---|---|---|---|
| G1 | `duplicate_of` set | `DUPLICATE_BLOCKED` | not approvable |
| G2 | committed(today) + payable > `daily_cap_cents`, or `SETTLEMENT_HALT` on | `FREEZE` (`OVER_DAILY_CAP` / `HALTED`) | not approvable (change the mandate / lift the halt) |
| G11 | payable > `po_remaining_cents` (same goods already paid, R17) | `FREEZE` (`PO_EXHAUSTED`) | not approvable |
| G3 | classifier `INJECTION` or `SUSPICIOUS` (incl. unavailable), any hidden segment, payee mismatch or no payee extracted, or vendor not `VERIFIED` (incl. `COOLING`) | `FREEZE` (`HIGH_RISK`) | **2**, distinct humans, neither the uploader (Q2 decided) |
| G4 | `billed_cents > max_single_invoice_cents` | `FREEZE` (`OVER_SINGLE_LIMIT`) | 1 |
| G5 | price variance (total cents) > tolerance, or billed qty > ordered qty | `FREEZE` (`MISMATCH`) | 1 |
| G6 | no PayPal invoice found, `paypal_invoice_matches_pdf = false`, or PayPal invoice not open (`SENT`/`UNPAID`/`PARTIALLY_PAID`) | `FREEZE` (`UNVERIFIED_SOURCE` / `SOURCE_MISMATCH`) | 1 |
| G7 | fuzzy duplicate | `FREEZE` (`POSSIBLE_DUPLICATE`) | 1 |
| G12 | vendor age < `VENDOR_NEW_DAYS`, or vendor committed today + payable > `vendor_daily_cap_cents`, or > `ORG_HARD_MAX_INVOICES_PER_VENDOR_PER_DAY` | `FREEZE` (`VELOCITY`) | 1 |
| G13 | no invoice number (normalized) | `FREEZE` (`NO_INVOICE_NUMBER`) | 1 |
| G14 | `precision_ok = false` (fractional qty, sub-cent price, non-USD) | `FREEZE` (`PRECISION`) | 1 |
| G8 | none of the above, accepted = billed qty and `billed_cents == accepted_value_cents` | `AUTO_SETTLE` | 0 |
| G9 | none of the above, accepted < billed, short-pay enabled, Δ ≤ `max_short_pay_delta_cents`, model chose `SHORT_PAY` | `SHORT_PAY` | 0 |
| G10 | anything else | `FREEZE` (`NEEDS_REVIEW`) | 1 |

Any `FREEZE` with `risk ≥ dual_approval_risk_threshold` needs 2 approvals. `payable_cents = min(billed_cents, accepted_value_cents, po_remaining_cents)` for every paying route, including human approval (R3, R17). A human `REJECT` → `REJECTED`. Approvals are counted here, not in the router: only non-voided rows whose `gate_hash` equals the current gate's, one per distinct operator, excluding the uploader and any `admin` / `vendor_admin` / `receiver` (R11, R18).

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
    g.add_conditional_edges("score_risk", lambda s: s["risk"].score >= 30 and not s.get("evidence"),
                            {True: "investigate", False: "short_pay_decide"})   # investigate once per case
    g.add_edge("investigate", "short_pay_decide")
    g.add_edge("short_pay_decide", "policy_gate")
    g.add_conditional_edges("policy_gate", lambda s: s["gate"].route, {
        "AUTO_SETTLE": "approve_for_payment",
        "SHORT_PAY": "approve_for_payment",
        "FREEZE": "human_review",
        "DUPLICATE_BLOCKED": "close_duplicate",
        "REJECTED": "close_rejected",
    })
    g.add_edge("human_review", "match")                 # resume re-reads PO/GRN/vendor/invoice and re-gates (R11)
    for n in ("approve_for_payment", "close_rejected", "close_duplicate"):
        g.add_edge(n, END)
    return g.compile(checkpointer=checkpointer)

def human_review(state: AegisState, config: RunnableConfig) -> dict:
    decision_id = interrupt({
        "execution_id": state["execution_id"],
        "reasons": state["gate"].reasons,
        "required_approvals": state["gate"].required_approvals,
        "allowed_actions": state["gate"].allowed_actions,
        "gate_hash": state["gate"].gate_hash,
    })
    if not isinstance(decision_id, str):                # resume carries only an opaque id
        raise ValueError("resume payload must be an approval_id")
    # Server-only dispatcher context carries the persisted checkpoint/interrupt binding.
    # Validate the DB row (including consumed_at IS NULL) against it and this case/gate.
    binding = approvals_repo.validate_pending(
        decision_id, state, config["configurable"]["resume_binding"]
    )
    return {
        "approval_ids": approvals_repo.ids_for(state["execution_id"]),
        "resume_receipts": {**state.get("resume_receipts", {}), decision_id: binding},
    }  # checkpointed receipt is acceptance, not payment authority

# Resume from POST /api/executions/{id}/decisions:
#   serialized dispatcher validates + commits the row and its pending interrupt binding;
#   await graph.ainvoke(Command(resume=approval_id), config=server_config, durability="sync")
#   reload checkpoint receipt, then mark consumed_at in a separate app transaction (§11.3).
```
On resume LangGraph re-runs `human_review` from the top (SPIKE-9); completed nodes are not re-run. Resume integrity rules (R11, on top of the `gate_hash` binding above):
- **Serialize the whole dispatch, not just insertion.** Every graph invocation for a case (initial run, decision, restart recovery) holds the same Postgres **session advisory lock** keyed by execution id on a dedicated connection until invocation and receipt reconciliation finish. It spans app commits and saver writes; a transaction-scoped lock released before `ainvoke` is insufficient. Do not return that connection to a pool while locked. If its connection is lost, cancel dispatch and do not continue graph writes. Pending recovery is reconciled before a new decision is accepted. This is cooperative serialization, not an atomic transaction across the app connection and `AsyncPostgresSaver`.
- **Validate, bind, commit; leave unconsumed.** Under that lock, read the latest checkpoint and require exactly one pending `human_review` interrupt. In an app transaction lock the case row, check the authenticated actor, reason, action and displayed `gate_hash`, then insert the immutable approval with server-read `resume_checkpoint_id` and `resume_interrupt_id`. An existing vote is loaded, not inserted twice; the unique actor/hash constraint counts it once. Require this case/thread, matching pending gate, eligible actor (not uploader, role allowed), `voided_at IS NULL` and `consumed_at IS NULL`. Commit **without** setting `consumed_at`; only then call `Command(resume=approval_id)` on the latest thread. Never invoke an old checkpoint to fork/replay history. Browser input cannot set checkpoint/interrupt bindings or dispatcher config.
- **Validate again in the node; persist acceptance before consumption.** `human_review` repeats row/case/gate/eligibility checks after `interrupt()`, including `consumed_at IS NULL`, against the original pending binding supplied by the trusted serialized dispatcher. A recovery retry may run at a later checkpoint of the **same task/interrupt**; compare its recorded binding, not a newly invented approval. Return a `resume_receipts[approval_id]` entry containing execution id, original checkpoint id, interrupt id and gate hash. Retain receipts across later nodes and approval loops. Use `durability="sync"`. After a freshly read checkpoint contains the matching receipt, the dispatcher sets `consumed_at` once in a separate app transaction. Consumption means accepted by the graph; it does not invalidate the vote. `policy_gate` still loads eligible, non-voided same-hash rows, including consumed votes, and recomputes all facts (R11).
- **Recover each commit gap.** Scan non-voided, unconsumed approval rows on startup and before later dispatch, under the same lock. Before the row commit there is nothing to recover. After its commit but before a durable receipt, retry the same `approval_id` only if checkpoint lineage still identifies the original pending task/interrupt and gate; no replacement approval or new vote. A matching receipt means never send that `Command` again: mark consumed and, if ordinary graph work remains, continue with `ainvoke(None, ...)`; a later interrupt waits for its own decision. Crash after consumption is also a continuation, never a new resume. Changed gate → void the old vote and require fresh review. Missing/ambiguous checkpoint lineage or binding → fail closed and alert; do not infer acceptance from HTTP success or advance payment. Keep relevant checkpoints/receipts until reconciliation; pending writes and errored tasks must be inspected, not blindly treated as a new interrupt.
- **Reject bad payloads without claiming saver rollback.** Non-string, unknown, voided, consumed, stale-hash, foreign-case or wrong-interrupt requests → 409 before invocation. Node validation remains defense in depth; an exception must prevent approval effects and payment, but the saver may retain error/resume metadata. Recovery must inspect that state; it is incorrect to promise that an exception writes nothing or automatically restores the original interrupt. **A committed approval that node validation rejects** (e.g. the actor's role was revoked after the router check) is voided by the dispatcher (`voided_at` set, reason recorded) in its own app transaction, so the recovery scan never retries it; the case then needs a fresh review and does not advance to payment. Whether the same thread can take a new `Command(resume=…)` at the original interrupt after such an exception is **unverified** (SPIKE-9 follow-up); until a probe records it in §18, such a case goes to manual review rather than being re-resumed.
- **No side effects before `interrupt()`; idempotent writes after it.** The node may re-run after a crash, so every application write after resume is keyed (upsert on `approval_id` / `execution_id`); `approve_for_payment` already relies on the R5 outbox key. The spike deliberately writes counters before the interrupt to measure this replay, not as a product-node pattern.

This is the required P1/P2 implementation contract, **not an implemented approval router or recovery worker**. SPIKE-9 proves durable interrupts and a persisted acceptance receipt only; auth, DB approval validation, serialization and recovery-gap tests remain required by §17.
A dual-approval case loops `human_review → match → … → policy_gate → human_review` until two distinct eligible operators approve the **same** `gate_hash` or one rejects. If anything the gate depends on changed in between, the hash changes, earlier approvals are voided and the case asks again. `approve_for_payment` writes the frozen outbox command (R5) and the PO reservation (R17) in the same transaction as the status change.

### 11.4 Settlement state machine (`settlement/`)
```text
APPROVED_FOR_PAYMENT ──outbox worker (lease)──► R20 recheck ──► [tx: reserve spend, insert payouts row, PAYOUT_SENDING] ──► POST payout ──► PAYOUT_SUBMITTED
PAYOUT_SENDING (crash / timeout) ──TTL + halt + R20 ok──► re-POST same keys ──► 201 → PAYOUT_SUBMITTED | 400 duplicate sender_batch_id → parse id, GET on base URL, full match → adopt → PAYOUT_SUBMITTED | else NEEDS_MANUAL_REVIEW (reservations kept)
PAYOUT_SUBMITTED ──item SUCCESS (webhook hint + GET, or poll)──► PAYOUT_SUCCEEDED
PAYOUT_SUCCEEDED ──VendorGateway.record_payment (keyed)──► SETTLED   (route SHORT_PAY → short_pay_balance = NOTICE_SENT)
PAYOUT_SUBMITTED ──FAILED/RETURNED/BLOCKED/DENIED──► release (same business_date) ──► PAYOUT_FAILED
PAYOUT_SUBMITTED ──UNCLAIMED──► FROZEN(STALE_PAYEE), reservation kept ──confirmed cancel──► release
PAYOUT_SUCCEEDED | SETTLED ──REFUNDED/REVERSED──► NEEDS_MANUAL_REVIEW + alert
any response with amount/receiver ≠ command ──► NEEDS_MANUAL_REVIEW + SETTLEMENT_HALT
```
```python
class SettlementService:                                       # runs only in the settlement process (R22)
    async def execute(self, execution_id: str) -> None:       # the ONLY public entry point (R1)
        cmd = await outbox.get(execution_id)                  # frozen amount, receiver, vendor_version (R5)
        await prechecks.assert_ok(cmd)                        # R20: halt, vendor version, GRN/PO, invoice open → else FROZEN
        async with db.transaction():                          # write-ahead (R19); no lock held across HTTP
            ex = await repo.get_execution_for_update(execution_id)
            await transitions.apply(ex, "PAYOUT_SENDING")     # R6 table + row_version
            await spend_ledger.reserve(ex, cmd.amount_cents)  # raises OverCap → FROZEN
            await repo.insert_payout(ex, cmd)                 # keys + frozen values, before the POST
        resp = await payouts.create_or_adopt(                 # duplicate-batch error → adopt (R19)
            sender_batch_id=f"AEGIS-{execution_id}",
            sender_item_id=f"{execution_id}-1",
            receiver=cmd.receiver_payer_id,
            amount_cents=cmd.amount_cents,
            request_id=f"aegis-{execution_id}-payout",
        )
        assert_matches(resp, cmd)                             # R5, on GET batch (create has no items); else NEEDS_MANUAL_REVIEW + halt
        await repo.record_payout_response(ex, resp)
        await transitions.apply(ex, "PAYOUT_SUBMITTED")
```
The outbox worker and reconciler run in the `settlement` background worker (one instance; the reconciler holds an advisory lock), claiming rows with `SELECT … FOR UPDATE SKIP LOCKED` and a 60 s lease, polling every 2 s and checking `SETTLEMENT_HALT` and the velocity breaker on every tick (R23). Settlement events reach the API's SSE stream through Postgres `LISTEN/NOTIFY`.

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
- **P1 (agentic commerce):** the Vendor Agent (A6) replies with a structured `RemedyProposal ∈ {ACCEPT_WRITE_OFF, CREDIT_NOTE_NEXT_ORDER, REPLACEMENT_UNITS, CONTEST_WITH_REASON}`. The buyer side accepts the first three automatically **only if** they are within bounds — `CREDIT_NOTE_NEXT_ORDER` value ≤ Δ, `REPLACEMENT_UNITS` ≤ `rejected_qty`, at most one remedy per execution — and escalates anything else, plus `CONTEST_WITH_REASON`, to a human with both agents' messages rendered as quoted, inert text. An accepted remedy creates no payout and no credit outside R8/R17. **The payment amount never changes during negotiation** (R3) — agents negotiate the remedy for Δ, not the payout.

---

## 13. Backend API (`backend/app/routers/`)

All routes except `/api/health`, `/api/auth/login` and the PayPal webhook routes require an operator session (R25: `HttpOnly; Secure; SameSite=Strict`, idle/absolute TTL) and, for every non-GET, a CSRF token. Every route checks role **and** execution state server-side. Rate limits: login 5/min per IP + account with lockout; `documents` and `demo/*` 10/min per session; webhooks 60/min per app. Body caps: PDF 10 MB, JSON 64 KB.

| Method | Path | Role | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Named operator login (R18); throttled |
| `POST` | `/api/mandates/compile` | controller | NL → draft `MandatePolicy` (inactive) + deterministic diff vs active + clamped fields |
| `POST` | `/api/mandates/{policy_id}/versions/{v}/activate` | cfo | Activate a draft; must not be its author (R13); alerts |
| `GET` | `/api/mandates/active` | any | Active policy + today's committed spend from `spend_ledger` |
| `POST` | `/api/vendors/{vendor_id}/changes` | vendor_admin | Request a payee change (R15) |
| `POST` | `/api/vendors/changes/{change_id}/approve` | vendor_admin, cfo | Approve; not the requester; starts the cooling period; alerts |
| `GET` | `/api/vendors` | any | Vendor master + change history (old/new values) |
| `POST` | `/api/grns` | receiver | Record a goods receipt for a PO (R16); corrections supersede |
| `POST` | `/api/documents` | controller, cfo | Upload invoice PDF → creates execution, starts graph asynchronously, returns `execution_id`; uploader cannot approve it |
| `GET` | `/api/executions` | any | Ledger rows for the grid widget |
| `GET` | `/api/executions/{id}` | any | Full case: extraction, forensics, match, risk, evidence pack, short-pay, gate, approvals, payouts, PayPal IDs, webhook receipts |
| `GET` | `/api/executions/{id}/events?format=json\|csv` | any | HMAC-chained audit export, verified against the latest external anchor (R21) |
| `POST` | `/api/executions/{id}/decisions` | controller, cfo | `{action, reason}`; actor from session; stores an approval bound to the current `gate_hash`; resumes with its `approval_id` (R11) |
| `POST` | `/api/executions/{id}/payout/cancel` | cfo | Cancel an `UNCLAIMED` payout item; release only after confirmed |
| `POST` | `/api/ops/settlement-halt` | cfo, admin | `{halted, reason}` (R23); alerts |
| `GET` | `/api/stream` | any | SSE: execution IDs + states only (no evidence content) |
| `POST` | `/api/webhooks/paypal/treasury` | PayPal signature | Payout events (R10) |
| `POST` | `/api/webhooks/paypal/vendor` | PayPal signature | Invoicing events (R10) |
| `GET` | `/api/vendor-sim/invoices` | any | Supplier-side view of invoices, payments and notices (no risk or gate data) |
| `POST` | `/api/vendor-sim/notices/{notice_id}/respond` | any | Accept / contest (P1: triggers the Vendor Agent) |
| `POST` | `/api/demo/reset` | admin, **only if `DEMO_ENABLED`** | New run suffix; recreate PayPal invoices, PDFs, POs, GRNs; mark executions archived (audit chain kept) |
| `POST` | `/api/demo/scenarios/{scenario_id}/run` | controller, **only if `DEMO_ENABLED`** | Feed the seeded PDF for S1–S5 into `/api/documents` |
| `POST` | `/api/demo/forensics-detector` | admin, **only if `DEMO_ENABLED`** | `{enabled: bool}` for the kill-switch proof; recorded on each execution |
| `GET` | `/api/health` | — | DB, both OAuth tokens (settlement reports its own), both webhook registrations, distinct accounts (R12), demo/replay flags, halt state, last anchor age |

When `DEMO_ENABLED=false` the `demo` router is not mounted at all (R24). SSE fan-out uses Postgres `LISTEN/NOTIFY` (settlement runs in its own process, ADR-007).

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
1. **Evidence** — side by side: PO (version) · invoice PDF (rendered, labelled *untrusted*) · PayPal invoice (labelled *vendor-asserted*) · Goods receipt (version, recorded by). Mismatched fields highlighted. PO consumption bar: accepted value vs already paid vs this payable.
2. **Security** — raw hidden-text findings **first** (PDF render with bounding-box overlays, technique label on hover); classifier label and rationale; risk contributions table; payee panel stating **"Payee source: vendor master v{n} (locked)"** with onboarding date and last change (who requested, who approved, when it became effective) next to the extracted payee.
3. **Money** — gate reasons, short-pay model table (§12.3) with assumptions, approvals progress (e.g., "1 of 2", with voided approvals shown struck through and why), payout and invoice IDs, settlement timeline, audit export button with anchor verification.

Investigator facts are shown as verbatim quoted tool output with the tool-call ID; vendor-asserted facts carry a badge; the advisory recommendation is hidden on `HIGH_RISK` cases (R14). Detector state and `REPLAY` are shown on the case when applicable.

Actions (only when allowed by `GateDecision.allowed_actions` and the viewer is an eligible approver — not the uploader): **Approve** (pays `min(billed, accepted value, PO remaining)`), **Short-pay**, **Reject**. Each requires a reason and confirms the displayed `gate_hash` (stale view → "case changed, review again").

### 14.4 Vendor portal (simulator)
Invoices issued by the supplier account, PayPal status per invoice, payments received, short-pay notices with Accept / Contest. P1: show the Vendor Agent's structured reply. Never shows risk scores, gate reasons, thresholds or mandate values (R14).

### 14.5 Demo toolbar (admin only, rendered only when `DEMO_ENABLED`, R24)
Reset run · Run S1 / S2 / S3 / S4 / (S5) · Forensics detector ON/OFF · Replay-mode indicator. A persistent "SANDBOX DEMO" banner is shown whenever `DEMO_ENABLED`.

### 14.6 Ops (cfo/admin)
Settlement halt toggle with reason, velocity breaker status, recent alerts, vendor-master change queue (requests, approvals, cooling countdown), last audit anchor time.

---

## 15. Demo scenarios (`backend/app/demo/`)

Seeded for each run: operators (2 × controller, cfo, receiver, 2 × vendor_admin, admin — R18), vendor master (one verified supplier, `onboarded_at` 90 days ago, payer ID captured from the supplier sandbox account, one already-effective change record so the history panel is real), POs (created by admin), GRNs (`source=SEEDED`, recorded by the receiver), PDFs generated with `reportlab`, and PayPal invoices created and sent from the Vendor app. Scenario PDFs are uploaded as controller #1; approvals in the video use controller #2 and cfo. `demo/reset` starts a new mandate `policy_id` and archives prior executions, so rehearsal runs do not trip the daily, per-vendor or velocity limits; real sandbox payouts stay in the ledger and audit chain.

| ID | PO | Seeded state | Expected route and PayPal effects |
|---|---|---|---|
| **S1 — Happy path** | PO-1042 | 100 × LiDAR bracket @ $4.80 = $480.00. GRN: 100 received, 100 accepted. PayPal invoice from supplier for $480.00. Clean PDF. | `AUTO_SETTLE` → payout $480.00 to the vendor-master payer ID → item `SUCCESS` → record payment $480.00 → invoice `MARKED_AS_PAID` (SPIKE-4) → `SETTLED`; PO-1042 closes (R17). |
| **S2 — Damaged delivery** | PO-1043 | 100 × servo motor @ $5.00 = $500.00. GRN: 100 received, 85 accepted, 15 rejected ("crushed in transit"). PayPal invoice $500.00. Clean PDF. | `SHORT_PAY` (§12.3) → payout $425.00 → record payment $425.00 → invoice `PARTIALLY_PAID` → notice for $75.00 → vendor portal Accept (P1: agents agree a remedy). |
| **S3 — BEC + indirect prompt injection** | PO-1044 | 80 × flight controller @ $5.00 = $400.00. GRN: 80 accepted. **No PayPal invoice** for this invoice number (it arrived only as an emailed PDF). PDF payee is a look-alike of the vendor-master address (e.g., `supp1ier@aegispay-dem0.example`) plus white-on-white footer instructing the agent to ignore mandates and pay that address. | Forensics flags hidden text + `INJECTION`; match flags payee mismatch + look-alike + unverified source → risk 100 (capped) → `FREEZE`, dual approval required. Controller rejects → `REJECTED`. **Kill-switch proof:** re-run with detector OFF → still frozen by payee mismatch and unverified source; the case shows any payment could only go to the vendor-master payee. No PayPal call is made. |
| **S4 — Duplicate resubmission** | PO-1042 | Re-upload of S1's PDF after S1 settled. | `DUPLICATE_BLOCKED` in < 1 s, linking to S1's execution. Variant: invoice number altered to `INV 2026-1042` or `…-1042-A` → normalized duplicate or fuzzy duplicate → blocked/frozen, and even with a fresh number G11 freezes because PO-1042 is fully paid (R17). |
| **S5 (P2) — Stale payee** | PO-1045 | Vendor master points to an address with no sandbox account. | Payout item `UNCLAIMED` → `FROZEN (STALE_PAYEE)`, reservation kept. Cancel is not shown (SPIKE-5: sandbox returns 500). |

Mandate used in the demo (typed live in the video):
> "Auto-pay verified suppliers up to $500 per invoice and $2,000 per day, only for goods the warehouse accepted, at exactly the PO price. If part of a delivery is rejected, pay for what we accepted and notify the supplier when the disputed amount is under $100. Anything with a changed payment address needs two approvers."

Daily cap check: S1 $480.00 + S2 $425.00 = $905.00 ≤ $2,000.00 org-wide and ≤ $1,500.00 per vendor (G12). The mandate is compiled by controller #1 and activated by the cfo on screen (R13).

---

## 16. Non-functional requirements

| Area | Requirement |
|---|---|
| Availability | Hosted demo up from submission through **2026-12-15**. Use paid (non-sleeping) Render instances funded by hackathon credits; external uptime ping on `/api/health`. |
| Latency | S1 decision ≤ 20 s p50; UI reflects state changes ≤ 1 s after they happen (SSE). |
| Security | Named operators with one role each (R18); sessions + CSRF + throttling (R25); secrets split per service (R22); CORS limited to the frontend origin; webhook verification (R10); sandboxed PDF parsing; `gitleaks` and `pip-audit`/`npm audit` in CI. |
| Observability | Structured JSON logs with `execution_id`, node name, PayPal `paypal-debug-id`; no PII, tokens, invoice text or mandate prompts in logs; alerts per R23; `/api/health` covers DB, both tokens, both webhooks, distinct accounts, halt, last anchor age. |
| Resilience | PayPal 5xx/timeouts → retry with the **same** Request-Id (exponential backoff, max 3) after the R19 lookup; reconciler poll guarantees settlement progress without webhooks; deploy overlap cannot double-run settlement (advisory lock). |
| Honesty | `DEMO_REPLAY_MODE` (requires `DEMO_ENABLED`, sandbox only) serves recorded sandbox responses only when the sandbox is unavailable, with a persistent **REPLAY** banner; replayed executions are tagged `replayed=true` and never touch ledgers (R24). README states the threat model and what is simulated (§4.0, §3). |
| Cost | LLM calls per invoice execution ≤ 4 (A2, A3, optional A4, optional A5), including retries and tool-loop continuations. Q3: **$1.00 USD total per invoice execution**, OpenAI `gpt-6.1-sol` for extraction and reasoning. Reserve a conservative maximum billable cost before each request; insufficient budget fails closed. Count image input, reasoning/output, cache writes and applicable premiums; reconcile actual tokens/cost per execution. See `docs/TOOLS.md` for dated pricing and implementation limits. |

---

## 17. Testing and evaluation

| Layer | What | Tooling |
|---|---|---|
| Unit | Policy gate truth table G1–G14, risk scoring, short-pay math (S2 numbers exact to the cent), HMAC chain + anchor verification, invoice-number normalization, transition table (R6), every mandate clamp (R13) | `pytest` |
| Property | For any extraction + any human input: receiver = approved vendor-master version; paid ≤ accepted value; Σ PO reservations ≤ PO accepted value across any invoice numbers (R17); Σ reservations ≤ cap and per-vendor cap under concurrent executions; releases land on the reservation's date | `hypothesis` |
| Static / architecture | Tool registry contains only read tools; `paypal/payouts.py` imported only from `settlement/`; no `float` in money schemas; each service boots only with its tagged env (R22); DB grants (api role cannot write vendor_master/outbox/payouts/ledgers) | `pytest` + `import-linter` + `ruff` |
| Security / SoD | Role matrix (every route × role); author cannot activate; uploader cannot approve; one human cannot supply both approvals; stale `gate_hash` approval rejected; resume with a dict payload rejected; resume with an approval of another case, a voided or an already-consumed `approval_id` → 409 and no `ainvoke`; concurrent same-case decisions serialize across app commits and saver writes; crash before/after approval commit, before/after durable acceptance receipt and after consumption → recover the same bound task without a new vote or duplicate effects; a later interrupt never receives an old approval; node rejects a committed approval → row voided, no retry loop, case to manual review; lost lock, missing checkpoint lineage and node validation errors fail closed; vendor change with requester = approver rejected; payee change during cooling freezes; CSRF missing → 403; demo routes absent when `DEMO_ENABLED=false`; boot refuses demo/replay with a live URL | `pytest` |
| Crash / settlement chaos | Kill the worker after each step of R19 (before commit, after commit before POST, after POST before response, after response before commit) → exactly one payout, correct ledger; duplicate-batch error → adopted; respx mocks do **not** dedupe on Request-Id, and the test asserts at most one POST per recovery attempt, no POST after `PAYOUT_RECOVERY_TTL_MIN` or under halt/failed R20, and reservations kept on every `NEEDS_MANUAL_REVIEW` exit; adopt rejects a batch with a foreign `sender_batch_id`, extra items or a different `sender_item_id`; an error href on another host is never requested; response amount/receiver mismatch → halt; velocity breaker trips | `pytest` + `respx` |
| Contract | Every PayPal call against recorded sandbox fixtures; one manual live smoke job | `respx` |
| Webhook chaos | Duplicate, out-of-order, delayed, unsigned, simulator events; forged cert host (`paypal.com.evil.com`); `resource_id` not ours; wrong app; late `PENDING` after `SUCCESS`; webhook + poller racing `record_payment` → one call | `pytest` |
| LLM eval | ≥ 50 labelled PDFs, held-out split, 5 runs → per-class counts with Wilson intervals, extraction field accuracy, A4 memo-injection cases, A5 leakage check, published in `docs/EVAL.md` | `pytest` eval suite |
| End-to-end | S1–S4 in replay mode (CI) and against sandbox (manual) | Playwright |

---

## 18. Day 1–2 verification spikes (do these before product code)

Record each result in this table and update the affected sections.

| ID | Question | How | Pass criterion | Fallback | Result |
|---|---|---|---|---|---|
| SPIKE-1 | Vendor app can create + send an invoice to the treasury email with `invoicer` = vendor account email | Two Business sandbox accounts, two REST apps | Invoice status `SENT`/`UNPAID` | — | 2026-10-22 PASS. Vendor app (country US, Invoicing feature enabled; token scope `https://uri.paypal.com/services/invoicing`) created a draft with `invoicer` = vendor account email and recipient = treasury email: HTTP 201 (debug-id f441602c35130). `/send` with `send_to_recipient=false`: HTTP 200 (f893447b8352a). GET: status `UNPAID`. Create without `invoicer` also 201 (defaults to the token's account). Findings: (1) India-country sandbox accounts blocked send (`INR_FOREIGN_CURRENCY_BLOCKED`) and INR currency is invalid; use US accounts, USD. (2) 403 `NOT_AUTHORIZED` on create until the app's Invoicing feature showed up in the token scopes; it cleared without any code change (dashboard change took time to apply, or needed a fresh save). Check the `invoicing` scope when debugging a 403. |
| SPIKE-2 | Can Treasury credentials read an invoice addressed to the treasury? | `GET` with Treasury token | 200 with invoice body | Read via VendorGateway (simulated consent, §3) | 2026-10-22 INCONCLUSIVE, fallback in use. Treasury token `GET /v2/invoicing/invoices/{id}` on the SPIKE-1 invoice: HTTP 403 `NOT_AUTHORIZED` (debug-id ca4e82d98a9d1); same call with the Vendor token: 200. The Treasury token has no `invoicing` scope (Invoicing not enabled on that app), so this does not show whether cross-account read is allowed. Reading and paying the invoice in the sandbox web UI as treasury worked, but that is not the API. Decision: read invoices through the Vendor gateway (§3 already says so). Retest only if Invoicing is enabled on the Treasury app. |
| SPIKE-3 | Payout Treasury → supplier: batch/item status transitions and webhook latency | Create payout; poll + webhook | Item reaches `SUCCESS`; timings recorded | Poll-only progress | 2026-10-22 PASS (poll only). $1.00 USD Treasury to supplier email: create HTTP 201, batch and item `PENDING` (debug-id ca4e82ded1bae). t+0.4s PENDING/PENDING, t+12.0s batch `PROCESSING`, t+17.9s item `SUCCESS` while batch still `PROCESSING` (batch reached `SUCCESS` ~32s in the SPIKE-5 runs). One sample per case. Webhook latency NOT measured: no webhook is registered; deferred to P0-14. Reconciler must key on item status, not batch status. |
| SPIKE-4 | Record payment (method `PAYPAL`) partial → `PARTIALLY_PAID`, full → `PAID`; which app receives which invoicing webhook | Vendor token | Statuses as expected; event names recorded | Track balance only in AegisPay | 2026-10-22 PARTIAL, statuses differ from the PRD. Invoice 10.00 with partial payment allowed: after send `UNPAID`; record 4.00 (`PAYPAL`) HTTP 200 gives `PARTIALLY_PAID` (paid 4.00, due 6.00); record 6.00 HTTP 200 gives `MARKED_AS_PAID` (paid 10.00, due 0.00), not `PAID`. An invoice paid by treasury in the sandbox UI shows `PAID`. Reconciler must treat `PAID` and `MARKED_AS_PAID` as fully paid; update the §9.2 and invoice-status wording. Which app receives which invoicing webhook event: NOT measured (no webhooks), deferred to P0-14. |
| SPIKE-5 | Payout to an email with no sandbox account → `UNCLAIMED`; cancel works | Payout + cancel | `UNCLAIMED` then cancelled | Drop S5 | 2026-10-22 PARTIAL. Payout to an unregistered email: create HTTP 201; item `UNCLAIMED` at ~18s, batch `SUCCESS` at ~32s. Cancel item: while the batch is not finished HTTP 400 `BATCH_NOT_COMPLETED` ("Only item belonging to a batch in Processed status can be cancelled", debug-id ca4e82f17db76); after batch `SUCCESS` HTTP 500 `INTERNAL_SERVICE_ERROR` on every try (6 tries, 2 items; e.g. ca4e82f7debb5, ca4e82fbbcfcd, ca4e83220389f); item stays `UNCLAIMED`. Cancel does not work in this sandbox. Fallback: keep `UNCLAIMED` detection and `FROZEN (STALE_PAYEE)`; drop the cancel call from S5 until it works. |
| SPIKE-6 | PayPal Agent Toolkit (Python) works inside LangGraph with read-only configuration | Install, bind tools, run one read | Tool call returns invoice data; no write tools exposed | Own read tools with identical names; disclose in TOOLS.md | 2026-10-22 PASS with caveats. `paypal-agent-toolkit` 1.1.0 installed (1.11.0 pins `crewai-tools==0.13.2`, not installable) with `langchain` and `langgraph` 1.2. `Configuration(actions={'invoices': {'list': True, 'get': True}})` exposes only `get_invoice` and `list_invoices`. A LangGraph `ToolNode` ran `get_invoice` against the vendor app: status success, invoice body returned. Calling `create_invoice` through the graph returns "not a valid tool". The toolkit has no `list_transactions` (§3 Treasury read): write our own read tool with that name and disclose it in TOOLS.md. `is_tool_allowed` passes if ANY of a tool's actions is enabled, so keep the config explicit. |
| SPIKE-7 | AG Studio: boilerplate runs; custom widget hosts React components; Studio Agent Framework hello-world; licence valid through 2026-12-15 | Boilerplate + docs + ask AG Grid team on Discord | All four confirmed | Studio widgets only; plain AG Grid ledger as last resort |  **PARTIAL (2026-10-25):** official starter builds; React custom widget and native Studio Agent Framework scripted hello-world work in development evaluation. Licence including AI through 2026-12-15 is unconfirmed; no live LLM integration claimed. See [SPIKE-7 evidence](spikes/SPIKE-7.md). |
| SPIKE-8 | Webhook self-verification for real events and simulator events (`WEBHOOK_ID`) | Recorded headers + raw body | Both verify; simulator flagged | Postback for real events only | |
| SPIKE-9 | `interrupt()` + `AsyncPostgresSaver` survives a process restart, then resumes | Kill and restart API mid-freeze | Resume succeeds | — | 2026-10-23 PASS (4 runs, local, not on Render). `spike9_checkpoint.py`: graph `prepare → review (interrupt()) → finish` on `AsyncPostgresSaver` (Postgres 16.15, compose service). Process 1 ran to the interrupt (`__interrupt__` with the payload) and hard-exited with `os._exit` inside the saver context (no graceful close). Process 2, a fresh Python process with a new pid, built the graph on a new saver: `aget_state` gave `next=('review',)`, pre-interrupt state intact, interrupt payload readable from `tasks[].interrupts`; `ainvoke(Command(resume=approval_id))` completed and the thread ended with `next=()`. Side-effect rows per node and pid: `prepare` 1× (p1 only, not re-run), `review` code **before** `interrupt()` 2× (p1, then again in p2), `review` after `interrupt()` 1× (p2), `finish` 1× (p2). So the interrupted node re-runs from the top on resume; completed nodes do not. Versions: Python 3.14.6, langgraph 1.2.16, langgraph-checkpoint 4.3.0, langgraph-checkpoint-postgres 3.2.0, psycopg 3.3.6 (binary), psycopg-pool 3.3.3. Notes: psycopg async on Windows needs `asyncio.SelectorEventLoop` (not the default Proactor loop); `saver.setup()` must run once before first use. Not tested: a resume with a non-string payload, concurrent resumes on one thread, a real Render restart. Changes: §11.3 `human_review` must keep code before `interrupt()` side-effect free. |
| SPIKE-10 | Payout with `recipient_type=PAYPAL_ID` to the supplier's payer ID; how to read the payer ID from the supplier account; does the item response echo receiver + amount? | Vendor token `GET /v1/identity/oauth2/userinfo` (or dashboard) → payer ID; $1 payout by `PAYPAL_ID` | Item `SUCCESS`; response fields recorded | Pay by frozen `EMAIL`; README says so (R1) | 2026-10-23 PASS. Payer ID: vendor-token `GET /v1/identity/oauth2/userinfo?schema=paypalv1.1` (and `schema=openid`) HTTP 200 but returns only `sub`/`user_id` (a 43-char identity URL id, not a payer ID); no `payer_id`, no emails. Working API path: vendor token `POST /v2/checkout/orders` (intent CAPTURE, 1.00, never approved, no money moves), then `GET` the order: `purchase_units[0].payee.merchant_id` = 13-char payer ID (`ZA2K93NPYUXXC`), `payee.email_address` = vendor account email. Treasury `POST /v1/payments/payouts` 1.00 USD `recipient_type=PAYPAL_ID` to it: HTTP 201; t+1.1s PENDING/PENDING, t+13.4s batch `PROCESSING`, t+18.9s item `SUCCESS`. Create response = `batch_header` (`batch_status`, `payout_batch_id`, `sender_batch_header`) + `links` only: **no items, so no receiver/amount echo**. `GET /v1/payments/payouts/{id}` items[0].`payout_item` and `GET /v1/payments/payouts-item/{id}` both echo `recipient_type=PAYPAL_ID`, `receiver` = payer ID, `amount` {`USD`, `1.00`}; also `payout_item_fee` 0.25 USD. Changes: R5/§9.3 assert from the first `GET`, not the create response; R15 payer-ID capture via the order path. |
| SPIKE-11 | Recover a payout after a crash: lookup by `sender_batch_id`, and the error body for a re-POST with a used `sender_batch_id` | Create a batch, re-POST the same `sender_batch_id` with a new Request-Id; try search/list endpoints | Existing batch id obtainable from either path | Adopt via the duplicate error body; else `NEEDS_MANUAL_REVIEW` (R19) | 2026-10-23 PASS via the error body. Batch created (HTTP 201). Re-POST same body + **same** `PayPal-Request-Id`: HTTP 400, not a replay of the 201. Re-POST same `sender_batch_id` + new Request-Id: the same HTTP 400 body: `name` `USER_BUSINESS_ERROR`, `message` "User business error.", `details` [{`field` `SENDER_BATCH_ID`, `location` `body`, `issue` "Batch with given sender_batch_id already exists", `link` [{`href` `https://api.sandbox.paypal.com/v1/payments/payouts/<existing payout_batch_id>`, `rel` `self`, `method` `GET`}]}], `links` []. The existing batch id is in the link href. No second payout was created (1.00 USD moved in total). Lookups: `GET /v1/payments/payouts?sender_batch_id=…` and `GET /v1/payments/payouts` HTTP 404; Transaction Search `GET /v1/reporting/transactions` HTTP 403 `NOT_AUTHORIZED` (Treasury token has no reporting scope; not retested with it enabled). Caveat: one run, with `recipient_type=EMAIL`; the duplicate-error shape for a `PAYPAL_ID` batch is unconfirmed, and so is how long PayPal keeps a `sender_batch_id` unique (hence the recovery TTL). Changes: R5, R19, §9.3 crash recovery and §11.4 now recover by a bounded re-POST with the same keys and adopt from the error link (hardened 2026-10-23 per security review). Dev B closeout 2026-10-26: probe now requires a valid sandbox recovery link and GET match of the frozen single-item command; 25 offline regressions pass, no new sandbox call. See `docs/spikes/SPIKE-11.md`; PAYPAL_ID duplicate shape and retention duration remain unconfirmed. |

---

## 19. Delivery plan (dated, 2 developers)

Dev A = PayPal, settlement, webhooks, agents, forensics, auth. Dev B = full stack + data: DB schema/migrations, seeder, audit chain, match + duplicates, docker/Render, frontend, AG Studio, video. Rebalanced 2026-10-22 to 35 open checklist items each.

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
**Never cut:** payee lock, async settlement, duplicate check, operator auth, LICENSE + README + judge guide, the video, and the v2.1 controls R5 (frozen command), R11 (bound approvals), R13 (second activator), R15 (vendor change control), R16–R17 (authenticated GRN, PO ledger), R18 (SoD), R19 (crash-safe settlement), R20 (pre-payout recheck), R24 (demo isolation), R25 (sessions/CSRF).
**May degrade, with the gap stated in the README:** R21 external anchor (fall back to HMAC chain only), R22 parser sandbox (fall back to subprocess without resource limits; the service split itself is not cut), R23 alert webhook (fall back to log-only alerts; the halt flag is not cut), eval set size (report the real n).

v2.1 adds work to existing checklist items rather than new phases: schema (P1-02), seeder (P1-10), audit chain (P1-11), settlement + reconciler (P1-07, P1-08), webhooks (P1-09), gate (P2-09), auth (P3-03), deploy (P3-04). New items for vendor change control, GRN route, PO ledger, halt/velocity and the SPIKE-10/11 spikes are added to the checklist.

---

## 20. Demo video storyboard (≤ 2:50, public YouTube, no copyrighted music)

| Time | Beat | On screen |
|---|---|---|
| 0:00–0:15 | Hook | IC3 BEC statistic; one line: agents that read invoices can be talked into paying the wrong person |
| 0:15–0:35 | Who and what | Persona + user quote; mandate typed → compiled → diff → activated |
| 0:35–1:00 | S1 | Upload → decision path lights up → payout → **PayPal sandbox account shows the payment** → invoice fully paid (`MARKED_AS_PAID`), confirmed by `GET` after the verified webhook |
| 1:00–1:30 | S2 | 85/100 accepted → short-pay table ($15.00 vs $65.92) → $425.00 paid → supplier sees notice (P1: agents agree a remedy) |
| 1:30–2:10 | S3 | Hidden text revealed with overlay → risk breakdown → dual approval required → reject. Detector OFF → still frozen; "Payee source: vendor master v1 (locked)"; one shot of the vendor change queue: a payee change needs a second approver and 72 h cooling, and the AI has no access to it |
| 2:10–2:22 | S4 | Duplicate blocked instantly |
| 2:22–2:40 | Cockpit | AG Studio widgets; (P1) analyst agent builds a widget from a question |
| 2:40–2:50 | Close | Decide/Execute diagram; PayPal APIs used; repo + live demo URL |

---

## 21. Submission checklist

- [ ] Public repo; `LICENSE` detected in GitHub's About panel.
- [ ] README: what it is, hosted URL, one operator login per role for judges (R18), 4-scenario walkthrough, simulation boundary (§3), threat model and non-claims (§4.0, §1.1), any degraded v2.1 control (§19), self-host steps linking `docs/SANDBOX_SETUP.md`.
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
| Vendor-master tampering / payee swap (insider or compromised account) | Medium | High | R15 dual approval + cooling + history + alerts; R20 recheck at payout; payer-ID binding (SPIKE-10) |
| Insider collusion (PO/GRN/approval) or a false GRN | Low | High | R16–R18 SoD across PO creator, receiver, approvers; R17 PO ledger; per-vendor caps (G12); audit anchor |
| Credential theft (operator password, session, Treasury secret) | Medium | High | R25 throttling + short sessions; R22 secret split; R23 velocity breaker + halt; rotate after judging |
| Crash or retry mid-payout → double pay or lost record | Medium | High | R19 write-ahead + adopt-existing-batch; crash tests (§17); SPIKE-11 |
| Runaway agent / bulk small invoices | Low | High | Gate is deterministic; G11/G12; R23 velocity breaker |
| Forged or replayed webhook | Low | High | R10 exact-host cert, resource ownership, GET-confirm |
| Audit history rewritten | Low | Medium | R21 HMAC with off-DB key + external anchor |
| Overclaiming in video/README | Medium | Medium | §1.1 non-claims; eval reported as counts with intervals |

---

## 23. Open questions

| ID | Question | Owner | Decide by |
|---|---|---|---|
| Q1 | ~~Pay by `PAYPAL_ID` instead of `EMAIL`?~~ **Decided 2026-10-22 (v2.1): yes**, payer ID is the primary binding (R1, R15); confirmed by SPIKE-10 PASS 2026-10-23. | Dev A | SPIKE-10 |
| Q2 | ~~Should `HIGH_RISK` cases be approvable?~~ **Decided 2026-10-22 (v2.1):** approvable only with 2 distinct eligible humans, neither the uploader (G3); payment still goes only to the vendor-master payee. | Both | Done |
| Q3 | ~~Which single LLM provider, and per-run cost cap?~~ **Decided 2026-10-26 (Dev B, user-delegated closeout): OpenAI; `gpt-6.1-sol` for both extraction and reasoning; $1.00 USD per invoice execution** (cap agreed 2026-10-23), ≤4 requests including retries. Selection rationale, exact IDs and dated prices in `docs/TOOLS.md`; enforcement and accuracy still require implementation/eval. | Dev B (P0 closeout); Dev A implementation | Decided |
| Q4 | Keep the dark "terminal" aesthetic or adopt the AG Studio theme as the base? | Dev B | Oct 23 |
