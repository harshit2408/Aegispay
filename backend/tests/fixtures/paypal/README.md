# PayPal response provenance (P4-03)

`spike11-duplicate.json` transcribes the HTTP **400** response body recorded in
[`docs/PRD_SRS.md` §18, SPIKE-11](../../../../docs/PRD_SRS.md) from the
2026-10-23 sandbox run. The existing payout batch ID is replaced with `BATCHSPIKE11`;
all other documented fields and values are preserved. This is a transcription of the
documented response, not a fresh HTTP capture or a complete recording with headers.
The recorded payout used `recipient_type=EMAIL` and moved 100 cents in total.
See also [`docs/spikes/SPIKE-11.md`](../../../../docs/spikes/SPIKE-11.md).

The contract test feeds this error through the production `PayPalClient` and `Payouts`
recovery path. Its GET responses and outgoing `PAYPAL_ID` command are explicitly synthetic:
they test that adoption requires an exact frozen-command match, including receiver and amount.
They do not establish the provider's duplicate-error shape for `PAYPAL_ID` payouts.

P4-03 remains partial: fresh sanitized recordings of the payout/invoice lifecycle and the
`PAYPAL_ID` duplicate response are still missing. No tokens, credentials, personal email
addresses or private environment files were used to create this fixture.
