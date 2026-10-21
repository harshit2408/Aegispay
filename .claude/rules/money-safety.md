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

- **R1** Payout receiver = `vendor_master.verified_paypal_email` (or payer id). No parameter, LLM output, PDF field, PayPal invoice field or request body can set it. `SettlementService.execute(execution_id)` is the only public entry.
- **R2** `paypal/payouts.py` is imported only from `settlement/`. Do not add an import elsewhere — the import-boundary test will fail.
- **R3** Payable = `min(billed_cents, accepted_qty × po_unit_price_cents)`. Human approval may lower it, never raise it.
- **R4** `int` cents only. No `float` in `schemas/` or `settlement/`. `Decimal` only to format `"480.00"` for PayPal.
- **R5** Every PayPal POST has `request_id`; keys derive from `execution_id` + step; retries reuse them.
- **R6** Payout creation ⇒ `PAYOUT_SUBMITTED`. Only verified `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` or a poll showing `SUCCESS` ⇒ `PAYOUT_SUCCEEDED`. Failures release the reservation.
- **R7** Invoice `record payment` uses Vendor credentials via `vendor_sim/gateway.py` only, after payout success.
- **R8** Reserve in `spend_ledger` inside a transaction holding `SELECT … FOR UPDATE` on the active mandate.
- **R9** Duplicate prevention by partial unique indexes + fuzzy check. Never weaken an index to make a test pass.
- **R10** Webhooks verified from raw bytes; cert host must end in `.paypal.com`; simulated events never change money state.
- **R11** Actor from session; reason ≥ 10 chars; dual approval by distinct operators when risk ≥ threshold.

Every change here needs a test that would fail if the rule broke (unit, `hypothesis` property, or `respx` contract).
Run `ecc:security-reviewer` on the diff before pushing.
