---
paths:
  - "backend/app/settlement/**"
  - "backend/app/paypal/**"
  - "backend/app/vendor_sim/**"
  - "backend/app/schemas/**"
  - "backend/app/models/**"
  - "backend/app/decision/policy_gate.py"
  - "backend/app/decision/short_pay.py"
  - "backend/app/routers/executions.py"
  - "backend/app/routers/webhooks.py"
  - "backend/tests/property/**"
---
# Money-safety rule (loaded automatically for money code)

You are editing code that moves or decides money. PRD §4 rules are law here:

- **R1** Payout receiver = the vendor-master payer ID frozen into the outbox command at approval (`recipient_type=PAYPAL_ID`, email fallback only if SPIKE-10 fails). No parameter, LLM output, PDF field, PayPal invoice field or request body can set it. `SettlementService.execute(execution_id)` is the only public entry.
- **R2** `paypal/payouts.py` is imported only from `settlement/`. Do not add an import elsewhere — the import-boundary test will fail.
- **R3** Payable = `min(billed_cents, accepted_qty × po_unit_price_cents, po_remaining_cents)`; `billed` from the structured PayPal invoice, PDF is a cross-check only. Human approval may lower it, never raise it.
- **R4** `int` cents only. No `float` in `schemas/` or `settlement/`. `Decimal` only to format `"480.00"` for PayPal.
- **R5** Every PayPal POST has `request_id`; keys derive from `execution_id` + step; retries reuse them. Amount, receiver and vendor version are frozen in the outbox row (immutable); assert PayPal's response matches.
- **R6** Every status change goes through `transitions.apply` (allowed-pairs table + `row_version`). Only a confirmed item `SUCCESS` (webhook hint + `GET`, or poll) ⇒ `PAYOUT_SUCCEEDED`. Late reversals ⇒ `NEEDS_MANUAL_REVIEW`.
- **R7** Invoice `record payment` uses Vendor credentials via `vendor_sim/gateway.py` only, after payout success.
- **R8** Reserve in `spend_ledger` inside a transaction holding `SELECT … FOR UPDATE` on the active mandate. Releases reference their reserve entry and keep its `business_date`; `UNCLAIMED` stays reserved until a cancel is confirmed.
- **R9** Duplicate prevention by partial unique indexes on the **normalized** invoice number + fuzzy check. Never weaken an index to make a test pass.
- **R17** PO consumption via `po_ledger`: Σ reserved ≤ accepted qty × PO price.
- **R10** Webhooks verified from raw bytes; cert from an exact host allowlist over HTTPS; `resource_id` must be ours; a webhook is a hint — confirm with `GET` before any money transition; simulated events never change money state.
- **R11** Actor from session; reason ≥ 10 chars; approvals bound to `gate_hash`, resume by `approval_id` only; distinct eligible humans counted inside `policy_gate`.
- **R19–R20** Write-ahead `PAYOUT_SENDING` before the POST, never hold a DB lock across HTTP, adopt an existing batch on duplicate; recheck halt, vendor/PO/GRN versions and invoice status right before paying.
- **R22–R23** Treasury secret only in the `settlement` process; check `SETTLEMENT_HALT` every tick.

Every change here needs a test that would fail if the rule broke (unit, `hypothesis` property, or `respx` contract).
Run `ecc:security-reviewer` on the diff before pushing.
