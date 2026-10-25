---
tags: [aegispay, checklist]
source: "[[PRD_SRS]] §18–§21"
---
# AegisPay — Shared Checklist

Single place both developers and both sets of agents read to know **what is done and what is next**.
The [[PLAN]] explains the why; this file is the state.

## How to use (humans and agents)

- **Line format is parsed by `.claude/hooks/session-start.mjs` — keep it exact:**
  `- [ ] **P1-03** @A Short description` — ID, owner (`@A`, `@B`, `@AB`), text.
- **Claim** before starting: append ` (WIP A)` or ` (WIP B)` to the line, commit, push. Never work on a line the other dev has claimed.
- **Finish**: change `[ ]` to `[x]`, drop the WIP tag, add the short commit SHA at the end: ` — a1b2c3d`.
- **Blocked**: append ` (BLOCKED: reason)` and log it in [[SESSION_LOG]].
- New work discovered mid-phase gets a new ID at the end of its phase; never renumber.
- Dev A = PayPal clients, settlement + money movement, webhooks, LangGraph + AI agents, forensics, auth. Dev B = full stack + data: DB schema/migrations, seeder, audit chain, match + duplicates, docker/Render, frontend + AG Studio, video (PRD §19).

---

## P0 — Setup and spikes (Oct 21–22) · exit: spike table filled, go/no-go per assumption

- [x] **P0-01** @AB Claude harness: CLAUDE.md, plan, checklist, hooks, routing, ponytail/caveman/obsidian, ECC memory
- [x] **P0-02** @B Add `LICENSE` (MIT) and confirm GitHub's About panel detects it — 2062c25
- [x] **P0-03** @B CI: ruff, pytest, `gitleaks`, frontend build on every push — 16cf6f9
- [ ] **P0-04** @B Render project + Render Postgres provisioned; claim Render credits
- [x] **P0-05** @A Claim APIMatic participant subscription; install the PayPal Context Plugin; log in `docs/TOOLS.md` — subscription claim form deferred to SUB-04; plugin installed, covers Server SDK only (see TOOLS.md)
- [x] **P0-06** @A Two sandbox Business accounts + two REST apps (Treasury, Vendor); steps in `docs/SANDBOX_SETUP.md` — accounts, apps, .env done; webhook IDs deferred to P0-14
- [x] **P0-07** @A SPIKE-1 Vendor app creates + sends invoice to treasury email (`invoicer` = vendor account) — PASS, see PRD §18 SPIKE-1 — 1774589
- [x] **P0-08** @A SPIKE-2 Can Treasury credentials read an invoice addressed to the treasury? — RESULT: 403 on treasury token (inconclusive), vendor-gateway fallback; PRD §18 SPIKE-2 — be2c466
- [x] **P0-09** @A SPIKE-3 Payout Treasury → supplier: status transitions + webhook latency measured — PASS poll-only (item SUCCESS ~18s); webhook latency deferred to P0-14; PRD §18 SPIKE-3 — be2c466
- [x] **P0-10** @A SPIKE-4 Record payment partial → `PARTIALLY_PAID`, full → `PAID`; which app gets which event — PARTIAL: full payment gives MARKED_AS_PAID, not PAID; webhook events deferred to P0-14; PRD §18 SPIKE-4 — be2c466
- [x] **P0-11** @A SPIKE-5 Payout to unknown email → `UNCLAIMED`; cancel works — PARTIAL: UNCLAIMED confirmed, cancel fails (500); PRD §18 SPIKE-5 — be2c466
- [x] **P0-12** @A SPIKE-6 PayPal Agent Toolkit (Python) inside LangGraph, read-only config — PASS: read-only config exposes get_invoice/list_invoices; no list_transactions; PRD §18 SPIKE-6 — be2c466
- [ ] **P0-13** @B SPIKE-7 AG Studio boilerplate runs; custom widget hosts React; Studio Agent hello-world; licence valid to Dec 15
- [x] **P0-14** @B SPIKE-8 Webhook self-verification for real + simulator events; Dev B takeover authorized 2026-10-25; both sandbox apps registered and verification evidence in `docs/spikes/SPIKE-8.md` — a3d89ae
- [ ] **P0-15** @A SPIKE-9 `interrupt()` + `AsyncPostgresSaver` survives a process restart — PASS (4 local runs): state survives hard exit, resume completes; interrupted node re-runs from top on resume; R11 resume rules + `approvals.consumed_at` added to PRD; PR #3 (needs B review)
- [ ] **P0-16** @A Decide Q3: single LLM provider + per-run cost cap; record model IDs in `docs/TOOLS.md` — cost cap decided $1.00/run (2026-10-23); provider still open
- [ ] **P0-17** @AB Record every spike result in PRD §18 "Result" column; update affected PRD sections — SPIKE-1..6 probes in PR #1, SPIKE-10/11 in PR #2, SPIKE-9 in PR #3; SPIKE-8 recorded by B (2026-10-25, docs/spikes/SPIKE-8.md); SPIKE-7 (B) pending
- [ ] **P0-18** @B `docker-compose.yml` (postgres + api + frontend) and `.env.example` per PRD §7 (WIP B)
- [ ] **P0-19** @A SPIKE-10 payout by `PAYPAL_ID` to the supplier payer ID; how to read the payer ID; does the response echo receiver + amount (PRD §18, R1) — PASS: payer ID via order `payee.merchant_id` (userinfo has none); PAYPAL_ID payout item SUCCESS ~19s; only GETs echo receiver + amount; PR #2 (needs B review)
- [ ] **P0-20** @A SPIKE-11 crash recovery: find a batch by `sender_batch_id`; error body on a re-POST of a used `sender_batch_id` (PRD §18, R19) — PASS via 400 `SENDER_BATCH_ID` error body (batch id in `details[].link`); Request-Id not replayed; lookups 404/403; PR #2 (needs B review)

- [x] **P0-21** @B Codex harness for Dev B: shared context/rules/memory, native configuration and standup/handoff workflows — 4b71712
- [x] **P0-22** @B Port all `.claude/agents/` and `.claude/skills/` definitions to native Codex agents and skills, preserving complete instructions — b0901d8
- [x] **P0-23** @B Mirror every project `.claude/` file into `.codex/`, activate native rules/routing/hooks/plugins, configure Obsidian for Claude — 20693c8

## P1 — Foundations (Oct 23–27) · exit: **headless S1** (CLI upload → real sandbox payout → invoice `PAID`)

- [x] **P1-01** @B Backend skeleton: `pyproject.toml`, `config.py` (pydantic-settings), `db.py` (async SQLAlchemy 2.0) — 16cf6f9
- [ ] **P1-02** @B ORM models + migration for PRD §8, incl. partial unique indexes (R9) and `one_active_mandate`
- [ ] **P1-03** @A `paypal/client.py`: OAuth cache per app, mandatory `request_id` on POST, `paypal-debug-id` logging, retry with same Request-Id (R5)
- [ ] **P1-04** @A `paypal/payouts.py` (Treasury) + import-boundary test: importable only from `settlement/` (R2)
- [ ] **P1-05** @A `paypal/invoicing.py` + `vendor_sim/gateway.py`: create, send, get, record payment (R7)
- [ ] **P1-06** @A `settlement/spend_ledger.py` reserve/release with `SELECT … FOR UPDATE` + concurrency test (R8)
- [ ] **P1-07** @A `settlement/service.py` `execute(execution_id)` only (R1) + outbox worker (`SKIP LOCKED`, 2 s poll)
- [ ] **P1-08** @A `settlement/reconciler.py` item-status state machine (PRD §9.3) + 15 s / 30 min poll fallback (R6)
- [ ] **P1-09** @A `paypal/webhooks.py` self-verification from raw bytes (R10) + both webhook routes + `webhook_events` dedupe
- [ ] **P1-10** @B `demo/seeder.py`: vendor master, PO-1042…1044, GRNs, PayPal invoices with per-run suffix
- [ ] **P1-11** @B `audit/events.py` hash chain + verification (HMAC form lands in P2-26)
- [ ] **P1-12** @A EXIT: headless S1 passes against the sandbox
- [ ] **P1-13** @B App shell on the official PayPal + AG Grid boilerplate (Next.js App Router)
- [ ] **P1-14** @B `lib/api.ts` typed client generated from FastAPI OpenAPI
- [ ] **P1-15** @B Mocked Case view: Evidence · Security · Money panes (PRD §14.3)
- [ ] **P1-16** @B Three 15-min AP/finance conversations → `docs/USER_RESEARCH.md` (due Oct 27)
- [ ] **P1-17** @B Decide Q4: terminal aesthetic vs AG Studio theme as base
- [ ] **P1-18** @AB Decide Q2: are `HIGH_RISK` cases approvable in the demo config?
- [ ] **P1-19** @B `operators` table, five roles, SoD CHECKs (`activated_by <> created_by`, uploader/approver) and DB roles + grants for api vs settlement (PRD §8, R18, R22)
- [ ] **P1-20** @B `vendor_master` v2.1 (payer ID, version, onboarding evidence) + append-only `vendor_master_changes` with immutability trigger (PRD §8, R15)
- [ ] **P1-21** @A `settlement/po_ledger.py` atomic PO reserve/release + property test Σ ≤ accepted value (R17)
- [ ] **P1-22** @A Write-ahead `PAYOUT_SENDING`, leased outbox claims, adopt-existing-batch, frozen command + response assertion; crash tests at each step (R5, R19)
- [ ] **P1-23** @A `settlement/transitions.py` allowed-pairs table + `row_version`; pre-payout recheck (halt, vendor/PO/GRN version, invoice open) (R6, R20)
- [ ] **P1-24** @B Settlement as a separate Render background worker with its own env; boot env assertions per service (R22, ADR-007)

## P2 — Decision engine + core UI (Oct 28 – Nov 2) · exit: **headless S1–S4 pass**; first `docs/EVAL.md` numbers

- [ ] **P2-01** @A A2 Invoice Extractor: zero tools, delimited input, schema-validated, low confidence → `FREEZE` (R14)
- [ ] **P2-02** @A Forensics `pdf_structure.py`: colour-match, tiny font, off-page, invisible/covered, annotation/metadata
- [ ] **P2-03** @A Forensics `ocr_diff.py` (cut-order item 5 — keep PDF-structure checks if cut)
- [ ] **P2-04** @A Eval set ≥ 50 labelled PDFs (held-out split, adaptive + A4 memo-injection cases, 5 runs, Wilson intervals) incl. false-positive traps in `tests/fixtures/eval/`
- [ ] **P2-05** @A A3 Injection classifier (no tools) → `ForensicsReport`
- [ ] **P2-06** @B `match` node: PO ↔ PDF ↔ PayPal invoice ↔ GRN; exact + fuzzy duplicate (R9)
- [ ] **P2-07** @A `decision/risk.py` additive table (PRD §10.2), contributions exposed
- [ ] **P2-08** @A `decision/short_pay.py` (PRD §12); S2 numbers exact to the cent ($65.92 vs $15.00)
- [ ] **P2-09** @A `decision/policy_gate.py` G1–G10 table-driven + full truth-table tests
- [ ] **P2-10** @A `decision/graph.py` topology + `human_review` `interrupt()` + resume via `Command`; Postgres checkpointer
- [ ] **P2-11** @A Property tests (hypothesis): receiver ∈ vendor master; paid ≤ accepted value; Σ reservations ≤ cap
- [ ] **P2-12** @A A1 Mandate compiler + clamp to `ORG_HARD_*` + diff + explicit activation (R13)
- [ ] **P2-13** @A Routers: documents, executions, decisions (R11), mandates, demo, health, SSE stream
- [ ] **P2-14** @B S4 duplicate blocked in < 1 s with link to original execution
- [ ] **P2-15** @A EXIT: headless S1–S4 green; first numbers in `docs/EVAL.md`
- [ ] **P2-16** @B Widget: Payables ledger (triad pills, risk meter, status; row → case)
- [ ] **P2-17** @B Widget: Mandate gauge (committed vs daily cap)
- [ ] **P2-18** @B Widget: Exposure by reason (G2–G10)
- [ ] **P2-19** @B Case view wired to `/api/executions/{id}`; actions only from `allowed_actions`, reason required
- [ ] **P2-20** @B Mandate studio: compile → diff → activate
- [ ] **P2-21** @B `lib/sse.ts` live updates ≤ 1 s
- [ ] **P2-22** @A `routers/vendors.py`: payee change request → second approver → cooling → effective; alerts (R15)
- [ ] **P2-23** @A `routers/grns.py`: receiver-only GRN recording, supersede-on-correction, qty/date checks (R16)
- [ ] **P2-24** @A Gate G11–G14, approvals bound to `gate_hash`, resume by `approval_id` only, distinct-human count inside the gate (R11)
- [ ] **P2-25** @A `SETTLEMENT_HALT`, velocity breaker, `ops/alerts.py` (R23)
- [ ] **P2-26** @B Audit HMAC chain via `audit_queue` + hourly external anchor + export verification (R21)

## P3 — Agents, auth, deploy (Nov 3–6) · exit: **hosted S1–S4 green on Render**; rough-cut video Nov 5

- [ ] **P3-01** @A A4 Investigator: read-only Agent Toolkit tools, ≤ 6 calls, citation validator; startup assertion over tool names (R2)
- [ ] **P3-02** @A A5 Notice drafter: numbers filled by template; vendor accept/contest flow
- [ ] **P3-03** @A Operator auth (controller, cfo, admin), httpOnly session, rate limits, CORS to frontend origin only
- [ ] **P3-04** @B `render.yaml` + deploy by **Nov 4**; single worker; uptime ping on `/api/health`
- [ ] **P3-05** @A `DEMO_REPLAY_MODE` serving recorded responses with REPLAY flag
- [ ] **P3-06** @B Widget: Decision path via SSE (React Flow, or stepper per cut order)
- [ ] **P3-07** @B Widget: Settlement timeline with PayPal IDs, verified / simulated badges
- [ ] **P3-08** @B Vendor portal `/vendor` (labelled Simulator)
- [ ] **P3-09** @B Demo toolbar: reset, run S1–S4, forensics detector ON/OFF (kill-switch), replay banner
- [ ] **P3-10** @B Custom dark theme shared by Studio and the app
- [ ] **P3-11** @B (P1 feature) Treasury Analyst agent via Studio Agent Framework
- [ ] **P3-12** @B Rough-cut video recorded **Nov 5** (insurance)
- [ ] **P3-13** @A Sessions `SameSite=Strict` + TTL, CSRF tokens, login throttling/lockout, body caps, SSE minimal payload (R25)
- [ ] **P3-14** @A Sandboxed `parser` subprocess: empty env, no network, page/size/time/memory limits (R22)
- [ ] **P3-15** @B `DEMO_ENABLED` gating (router not mounted), `replayed=true` tagging, boot refusal with live URL (R24)
- [ ] **P3-16** @B Ops panel (halt, alerts, vendor change queue) + case-view v2.1 badges: untrusted/vendor-asserted, quoted A4 facts, voided approvals (PRD §14.3, §14.6)

## P4 — Hardening (Nov 7–8) · exit: **feature freeze Nov 8 EOD**

- [ ] **P4-01** @A (P1 feature) A6 Vendor agent remedy negotiation — only if everything above is green
- [ ] **P4-02** @A Webhook chaos tests: duplicate, out-of-order, delayed, unsigned, simulator
- [ ] **P4-03** @A Contract tests with `respx` against recorded sandbox fixtures
- [ ] **P4-04** @B Playwright E2E S1–S4 in replay mode, running in CI
- [ ] **P4-05** @B Polish + second video cut
- [ ] **P4-06** @AB Feature freeze; only bug fixes after this line

## P5 — Ship (Nov 9–11) · exit: **submitted Nov 11** (deadline Nov 12 12:00 PT)

- [ ] **P5-01** @A `README.md`: judge quickstart, hosted URL, operator logins, simulation boundary (verbatim §3)
- [ ] **P5-02** @A `docs/TOOLS.md`, `docs/SANDBOX_SETUP.md`, final `docs/EVAL.md`
- [ ] **P5-03** @B Judge guide panel in the app
- [ ] **P5-04** @B Final video < 3 min, public on YouTube, captions, no copyrighted music
- [ ] **P5-05** @AB Code freeze Nov 9 23:59 IST
- [ ] **P5-06** @AB Clean-clone verification from README only
- [ ] **P5-07** @AB Devpost text (problem, persona, real vs simulated); sponsor prize = AG Grid
- [ ] **P5-08** @AB **Submit** (≥ 24 h before deadline)

## SUB — Must stay true until judging ends (Dec 15)

- [ ] **SUB-01** @AB Hosted demo verified from a clean browser; stays up through 2026-12-15
- [ ] **SUB-02** @B AG Studio licence valid through 2026-12-15
- [ ] **SUB-03** @A Rotate sandbox credentials after judging
- [ ] **SUB-04** @A Submit APIMatic subscription claim form (public repo URL + usage summary from `docs/TOOLS.md`; claim only real plugin usage)

## Never cut (PRD §19)
Payee lock · async settlement · duplicate check · operator auth · LICENSE + README + judge guide · the video.
