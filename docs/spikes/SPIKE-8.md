# SPIKE-8 result — P0-14

**PASS, 2026-10-25, Dev B.** Both sandbox REST apps have registered webhook subscriptions.
Their IDs are saved in the ignored local `.env`; no credentials, client IDs, raw webhook
bodies, or signatures are committed. User authorized Dev B to finish P0-14; P1-09 remains
Dev A's application work.

The public listener used a temporary Cloudflare Quick Tunnel to a standalone loopback
probe, as permitted by PRD §9.4. This establishes the spike result without claiming Render
provisioning complete. Retarget both subscriptions when the tunnel changes or Render is ready.

## Measured verification

Final repeat deliveries all passed raw-byte RSA-SHA256 signature checks **and** signing
certificate chain validation. Real events used the receiving app's own registered ID;
simulator routes used the literal `WEBHOOK_ID` and recorded `simulated=true`.

| App | Source | Event | Receipt (UTC) | Transmission-to-receipt age |
|---|---|---|---|---|
| Treasury | Simulator | `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` | 13:22:13.032 | 2,032 ms |
| Vendor | Simulator | `INVOICING.INVOICE.PAID` | 13:22:14.317 | 1,316 ms |
| Treasury | Real, resend | `PAYMENT.PAYOUTS-ITEM.SUCCEEDED` | 13:22:13.366 | 1,366 ms |
| Vendor | Real, resend | `INVOICING.INVOICE.PAID` | 13:22:15.897 | 1,897 ms |

These are four observations, not a latency guarantee. They measure signed transmission
time to receipt before verification work; they are not transaction-to-webhook timings.
Original treasury receipts also verified: `PAYOUTSBATCH.PROCESSING`, `.SUCCESS`, and
`PAYOUTS-ITEM.SUCCEEDED`; the original invoice paid receipt verified after 42.2 seconds
of transmission age. Earlier rejected resource-shape attempts caused retries, so these
observations must not be treated as an unbiased delivery benchmark.

The real payout POST at **13:16:52.839 UTC** returned HTTP 201, batch `74DY3PWS93ZX2`.
Its first verified processing receipt was at 13:17:24.973 UTC (~32.1 seconds after request),
batch success at 13:17:34.726 (~41.9 seconds), and item success at 13:17:35.328 (~42.5 seconds).
An authenticated GET confirmed item `SUCCESS`, expected receiver, and USD `1.00`.
Only then was payment recorded against invoice `INV2-D22Q-L2C7-TLR7-B42H` (HTTP 200),
which ended `MARKED_AS_PAID`, due `0.00`. Total real sandbox payout: **$1.00**.
The vendor identity came from the authenticated vendor app's unapproved order's
`payee.merchant_id`; that order never moved money. No notification emails were requested.

## Contract findings for P1-09

- Sign `transmission_id|transmission_time|webhook_id|unsigned_decimal_crc32(raw_bytes)`.
  Re-serializing JSON changes the bytes and fails verification.
- `PAYMENT.PAYOUTS-ITEM.*` payloads identify the resource with `payout_item_id`, not a generic
  `id`; batch payloads may use `payout_batch_id` or `batch_header.payout_batch_id`.
- Real invoicing events wrap the invoice in `resource.invoice`; simulator invoice events
  can use `resource.id`. Support both rather than silently rejecting real invoices.
- Live event discovery confirmed treasury `.HELD` is a webhook event name. Do not infer
  event names from payout item status values such as `ONHOLD`.
- Vendor subscriptions include `INVOICING.INVOICE.PAID`, `.UPDATED`, and `.CANCELLED`;
  `.CREATED` and `.SENT` were additionally registered for the probe. Real `.PAID` was observed.
- Simulator postback verification is unsupported; self-verification works with `WEBHOOK_ID`.
  Keep simulator endpoints separate and never enqueue them for settlement.

## Certificate trust and test limits

The signing leaf was retrieved over validated HTTPS from the exact sandbox PayPal cert host.
Its public-CA path validated as leaf → **DigiCert EV RSA CA G2** → trusted system root, using
the expected signing hostname `messageverificationcerts.sandbox.paypal.com`. Leaf validity:
2026-01-27 through 2027-01-26; SHA-256 fingerprint:
`d85b1a413bfb827214727fd5843758aeebc8bfb9b449b0d962ee216b7be9c856`.
The intermediate was fetched from the fixed HTTPS DigiCert URL, never an arbitrary AIA URL.
Signing certificate issuer rotation requires updating and rerunning this spike.

Offline tests cover trusted/untrusted/expired certificate chains, raw-body and app-ID mismatches,
simulator isolation, timestamps, invalid algorithms/headers, body/certificate size limits,
redirect/error handling, cache reuse, actual PayPal resource shapes, and a Hypothesis property
that PayPal-looking suffix hosts are rejected. No product receiver, resource-ownership lookup,
database deduplication, money transition, production PayPal request, or Render deployment is
claimed by P0-14.

Sources: [PayPal self-verification](https://developer.paypal.com/api/rest/webhooks/rest/),
[Webhooks API](https://developer.paypal.com/docs/api/webhooks/v1/),
[Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
