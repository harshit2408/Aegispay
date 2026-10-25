# P0 spike evidence and decisions

PRD §18 is the authoritative result table. This index connects its eleven results to the
implementation decisions and their limits. Recorded sandbox observations are historical
evidence; the P0 closeout does not claim a fresh paid API run or completed product controls.

| Spike | Recorded result / evidence | Go/no-go for implementation |
|---|---|---|
| 1: Vendor invoicing | PASS, 2026-10-22; PRD §18, `spike1_invoice.py` | Go: US Business sandbox accounts, USD, Vendor credentials and Invoicing scope. |
| 2: Treasury invoice read | INCONCLUSIVE: HTTP 403 without Invoicing scope; PRD §18 | Go with VendorGateway read fallback. No claim that cross-account API reads are impossible; retest only after scope is enabled. |
| 3: Payout progress | PASS for item polling; first item success ~17.9 s. Later real webhook receipts in [SPIKE-8](SPIKE-8.md). | Go: item `SUCCESS`, not batch status, plus authenticated GET; retain polling. Timings are observations, not an SLA. |
| 4: Record payment | PARTIAL against original status assumption: partial → `PARTIALLY_PAID`, full → `MARKED_AS_PAID`; Vendor real paid event observed in [SPIKE-8](SPIKE-8.md). | Go with `PAID` and `MARKED_AS_PAID` as fully paid and GET-confirmed balance. No claim every invoice event was measured. |
| 5: Unknown recipient/cancel | PARTIAL: `UNCLAIMED` observed, cancellation returned 500 after batch completion; PRD §18 | No-go for a reliable cancel demo. Freeze stale payee and keep reservations until cancellation is confirmed; defer S5 cancellation. |
| 6: Read-only toolkit | PASS with toolkit 1.1.0 and explicit invoice get/list actions; PRD §18, `spike6_toolkit.py` | Go: explicit read allowlist. No native `list_transactions`; own read tool must be disclosed. No LLM was needed for the graph tool probe. |
| 7: Studio/framework | [PARTIAL](SPIKE-7.md): starter, React custom widget and native scripted hello work. | Go for development scaffold. Licence + AI entitlement through 2026-12-15 and licensed rebuild remain unverified; no live LLM claim. |
| 8: Webhook verification | [PASS](SPIKE-8.md), 2026-10-25: both apps, real and simulator raw-byte signatures and trusted certificate paths. | Go: self-verification and separate simulator handling. Retarget temporary subscriptions; product ownership lookup/dedupe/money integration remain P1-09. |
| 9: Durable interrupt | [PASS](SPIKE-9.md): hard exit, fresh-process resume, third-process durable receipt recovery. | Go: Postgres saver with replay-safe nodes and serialized receipt reconciliation in PRD §11.3. Product dispatcher/consumption and Render restart are not proved. |
| 10: Payer ID | PASS recorded 2026-10-23; PRD §18, `spike10_payer_id.py`. | Go: vendor-authenticated order `payee.merchant_id`; freeze `PAYPAL_ID` and verify GET item type, receiver, amount and currency. Create response alone is insufficient. |
| 11: Batch recovery | PASS via duplicate-error link for one EMAIL batch; PRD §18, `spike11_recovery.py`. | Go only with exact link parsing, GET full frozen-command match and bounded same-key recovery. PAYPAL_ID duplicate shape and uniqueness lifetime remain unconfirmed; unresolved recovery keeps reservations and requires manual review. |

P0-17 completion means every assumption has a recorded result and decision. It does not
close the account-dependent Render or Studio licence work, nor the later application tasks.
Do not turn PARTIAL/INCONCLUSIVE into PASS when composing the demo or submission.
