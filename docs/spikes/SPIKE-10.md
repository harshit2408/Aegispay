# SPIKE-10 — payer-ID payout closeout

P0-19, reviewed by Dev B on 2026-10-26. This is spike evidence and offline probe validation, not a settlement implementation.

## Observed sandbox result

The existing 2026-10-23 run in PRD §18 and [PR #2](https://github.com/harshit2408/Aegispay/pull/2) established:

- Vendor-token userinfo returned HTTP 200 with identity fields but no payer ID or email.
- A Vendor-created, unapproved order exposed the supplier payer ID at `purchase_units[0].payee.merchant_id`; its payee email matched the supplier account. The order was never approved or captured.
- A Treasury payout of `1.00` USD using `recipient_type=PAYPAL_ID` returned HTTP 201. The item reached `SUCCESS` at about 18.9 seconds.
- The create response had batch metadata and links only. Both subsequent batch and item GETs echoed `PAYPAL_ID`, the supplier payer ID, and `1.00` USD. These GETs, not the create response, are the R5 assertion source.

No additional sandbox payout or credential access occurred during this closeout. The original run is the live evidence; offline tests do not reproduce PayPal behavior.

## Probe correction and verification

The old probe could print PASS after polling SUCCESS even when a confirming GET failed or showed a different receiver, recipient type, amount or final item status. The corrected verdict requires HTTP 200, item SUCCESS and exact command fields from both GETs; absence of the item ID fails the probe.

The offline regression matrix proves the normal metadata-only create response passes and each contradictory GET, HTTP failure or missing item ID fails. Request ID, receiver, recipient type and the `1.00` USD command are asserted without network access. Existing common-helper tests cover the sandbox-host allowlist and redirect refusal.

Production still needs the frozen vendor-master/outbox command, GET assertions, immutable money command and manual-review/halt handling specified by R1/R5/R15 and §9.3. Passing this spike does not prove those application controls.
