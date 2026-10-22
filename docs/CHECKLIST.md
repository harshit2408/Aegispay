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
- Dev A = backend, PayPal, agents. Dev B = full stack (frontend + backend), AG Studio, video (PRD §19).

---

## P0 — Setup and spikes (Oct 21–22) · exit: spike table filled, go/no-go per assumption

- [x] **P0-01** @AB Claude harness: CLAUDE.md, plan, checklist, hooks, routing, ponytail/caveman/obsidian, ECC memory
- [ ] **P0-02** @B Add `LICENSE` (MIT) and confirm GitHub's About panel detects it
- [ ] **P0-03** @B CI: ruff, pytest, `gitleaks`, frontend build on every push
- [ ] **P0-04** @B Render project + Render Postgres provisioned; claim Render credits
- [x] **P0-05** @A Claim APIMatic participant subscription; install the PayPal Context Plugin; log in `docs/TOOLS.md` — subscription claim form deferred to SUB-04; plugin installed, covers Server SDK only (see TOOLS.md)
- [x] **P0-06** @A Two sandbox Business accounts + two REST apps (Treasury, Vendor); steps in `docs/SANDBOX_SETUP.md` — accounts, apps, .env done; webhook IDs deferred to P0-14
- [ ] **P0-07** @A SPIKE-1 Vendor app creates + sends invoice to treasury email (`invoicer` = vendor account) (WIP A)
- [ ] **P0-08** @A SPIKE-2 Can Treasury credentials read an invoice addressed to the treasury?
- [ ] **P0-09** @A SPIKE-3 Payout Treasury → supplier: status transitions + webhook latency measured
- [ ] **P0-10** @A SPIKE-4 Record payment partial → `PARTIALLY_PAID`, full → `PAID`; which app gets which event
- [ ] **P0-11** @A SPIKE-5 Payout to unknown email → `UNCLAIMED`; cancel works
- [ ] **P0-12** @A SPIKE-6 PayPal Agent Toolkit (Python) inside LangGraph, read-only config
- [ ] **P0-13** @B SPIKE-7 AG Studio boilerplate runs; custom widget hosts React; Studio Agent hello-world; licence valid to Dec 15
- [ ] **P0-14** @A SPIKE-8 Webhook self-verification for real + simulator events (needs webhook IDs + public host; P0-06 left them empty)
- [ ] **P0-15** @A SPIKE-9 `interrupt()` + `AsyncPostgresSaver` survives a process restart
- [ ] **P0-16** @A Decide Q3: single LLM provider + per-run cost cap; record model IDs in `docs/TOOLS.md`
- [ ] **P0-17** @AB Record every spike result in PRD §18 "Result" column; update affected PRD sections
- [ ] **P0-18** @A `docker-compose.yml` (postgres + api + frontend) and `.env.example` per PRD §7

## P1 — Foundations (Oct 23–27) · exit: **headless S1** (CLI upload → real sandbox payout → invoice `PAID`)

- [ ] **P1-01** @A Backend skeleton: `pyproject.toml`, `config.py` (pydantic-settings), `db.py` (async SQLAlchemy 2.0)
- [ ] **P1-02** @A ORM models + migration for PRD §8, incl. partial unique indexes (R9) and `one_active_mandate`
- [ ] **P1-03** @A `paypal/client.py`: OAuth cache per app, mandatory `request_id` on POST, `paypal-debug-id` logging, retry with same Request-Id (R5)
- [ ] **P1-04** @A `paypal/payouts.py` (Treasury) + import-boundary test: importable only from `settlement/` (R2)
- [ ] **P1-05** @A `paypal/invoicing.py` + `vendor_sim/gateway.py`: create, send, get, record payment (R7)
- [ ] **P1-06** @A `settlement/spend_ledger.py` reserve/release with `SELECT … FOR UPDATE` + concurrency test (R8)
- [ ] **P1-07** @A `settlement/service.py` `execute(execution_id)` only (R1) + outbox worker (`SKIP LOCKED`, 2 s poll)
- [ ] **P1-08** @A `settlement/reconciler.py` item-status state machine (PRD §9.3) + 15 s / 30 min poll fallback (R6)
- [ ] **P1-09** @A `paypal/webhooks.py` self-verification from raw bytes (R10) + both webhook routes + `webhook_events` dedupe
- [ ] **P1-10** @A `demo/seeder.py`: vendor master, PO-1042…1044, GRNs, PayPal invoices with per-run suffix
- [ ] **P1-11** @A `audit/events.py` hash chain + verification
- [ ] **P1-12** @A EXIT: headless S1 passes against the sandbox
- [ ] **P1-13** @B App shell on the official PayPal + AG Grid boilerplate (Next.js App Router)
- [ ] **P1-14** @B `lib/api.ts` typed client generated from FastAPI OpenAPI
- [ ] **P1-15** @B Mocked Case view: Evidence · Security · Money panes (PRD §14.3)
- [ ] **P1-16** @B Three 15-min AP/finance conversations → `docs/USER_RESEARCH.md` (due Oct 27)
- [ ] **P1-17** @B Decide Q4: terminal aesthetic vs AG Studio theme as base
- [ ] **P1-18** @AB Decide Q2: are `HIGH_RISK` cases approvable in the demo config?

## P2 — Decision engine + core UI (Oct 28 – Nov 2) · exit: **headless S1–S4 pass**; first `docs/EVAL.md` numbers

- [ ] **P2-01** @A A2 Invoice Extractor: zero tools, delimited input, schema-validated, low confidence → `FREEZE` (R14)
- [ ] **P2-02** @A Forensics `pdf_structure.py`: colour-match, tiny font, off-page, invisible/covered, annotation/metadata
- [ ] **P2-03** @A Forensics `ocr_diff.py` (cut-order item 5 — keep PDF-structure checks if cut)
- [ ] **P2-04** @A Eval set ≥ 12 labelled PDFs incl. false-positive traps in `tests/fixtures/eval/`
- [ ] **P2-05** @A A3 Injection classifier (no tools) → `ForensicsReport`
- [ ] **P2-06** @A `match` node: PO ↔ PDF ↔ PayPal invoice ↔ GRN; exact + fuzzy duplicate (R9)
- [ ] **P2-07** @A `decision/risk.py` additive table (PRD §10.2), contributions exposed
- [ ] **P2-08** @A `decision/short_pay.py` (PRD §12); S2 numbers exact to the cent ($65.92 vs $15.00)
- [ ] **P2-09** @A `decision/policy_gate.py` G1–G10 table-driven + full truth-table tests
- [ ] **P2-10** @A `decision/graph.py` topology + `human_review` `interrupt()` + resume via `Command`; Postgres checkpointer
- [ ] **P2-11** @A Property tests (hypothesis): receiver ∈ vendor master; paid ≤ accepted value; Σ reservations ≤ cap
- [ ] **P2-12** @A A1 Mandate compiler + clamp to `ORG_HARD_*` + diff + explicit activation (R13)
- [ ] **P2-13** @A Routers: documents, executions, decisions (R11), mandates, demo, health, SSE stream
- [ ] **P2-14** @A S4 duplicate blocked in < 1 s with link to original execution
- [ ] **P2-15** @A EXIT: headless S1–S4 green; first numbers in `docs/EVAL.md`
- [ ] **P2-16** @B Widget: Payables ledger (triad pills, risk meter, status; row → case)
- [ ] **P2-17** @B Widget: Mandate gauge (committed vs daily cap)
- [ ] **P2-18** @B Widget: Exposure by reason (G2–G10)
- [ ] **P2-19** @B Case view wired to `/api/executions/{id}`; actions only from `allowed_actions`, reason required
- [ ] **P2-20** @B Mandate studio: compile → diff → activate
- [ ] **P2-21** @B `lib/sse.ts` live updates ≤ 1 s

## P3 — Agents, auth, deploy (Nov 3–6) · exit: **hosted S1–S4 green on Render**; rough-cut video Nov 5

- [ ] **P3-01** @A A4 Investigator: read-only Agent Toolkit tools, ≤ 6 calls, citation validator; startup assertion over tool names (R2)
- [ ] **P3-02** @A A5 Notice drafter: numbers filled by template; vendor accept/contest flow
- [ ] **P3-03** @A Operator auth (controller, cfo, admin), httpOnly session, rate limits, CORS to frontend origin only
- [ ] **P3-04** @A `render.yaml` + deploy by **Nov 4**; single worker; uptime ping on `/api/health`
- [ ] **P3-05** @A `DEMO_REPLAY_MODE` serving recorded responses with REPLAY flag
- [ ] **P3-06** @B Widget: Decision path via SSE (React Flow, or stepper per cut order)
- [ ] **P3-07** @B Widget: Settlement timeline with PayPal IDs, verified / simulated badges
- [ ] **P3-08** @B Vendor portal `/vendor` (labelled Simulator)
- [ ] **P3-09** @B Demo toolbar: reset, run S1–S4, forensics detector ON/OFF (kill-switch), replay banner
- [ ] **P3-10** @B Custom dark theme shared by Studio and the app
- [ ] **P3-11** @B (P1 feature) Treasury Analyst agent via Studio Agent Framework
- [ ] **P3-12** @B Rough-cut video recorded **Nov 5** (insurance)

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
