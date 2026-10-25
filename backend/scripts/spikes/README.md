# SPIKE-8: sandbox webhook verification

This standalone probe records signature-verification evidence for P0-14. It does not
implement P1-09's application receiver, database deduplication, resource-ownership checks,
settlement enqueueing, or money transitions. Product code must not import it.

Install from the repository root:

```powershell
.venv/Scripts/python.exe -m pip install -e 'backend[dev]'
.venv/Scripts/python.exe -m pytest backend/tests/unit/test_spike8_webhooks.py
```

Set `TREASURY_WEBHOOK_ID` and `VENDOR_WEBHOOK_ID` in the process environment from your
local configuration. The probe does not load `.env`, needs no PayPal credentials, and
binds to loopback only. Keep receipt evidence outside the repository:

```powershell
.venv/Scripts/python.exe backend/scripts/spikes/spike8_webhooks.py --evidence "$env:TEMP/spike8-evidence.jsonl"
cloudflared tunnel --url http://127.0.0.1:8768
```

Register each HTTPS listener URL with its corresponding **sandbox** app using
`POST /v1/notifications/webhooks` (or update that subscription's URL with `PATCH`):

| App | Listener | Subscriptions |
|---|---|---|
| Treasury | `/api/webhooks/paypal/treasury` | Available `PAYMENT.PAYOUTSBATCH.*` and `PAYMENT.PAYOUTS-ITEM.*` events |
| Vendor | `/api/webhooks/paypal/vendor` | `INVOICING.INVOICE.PAID`, `.UPDATED`, `.CANCELLED`; `.CREATED` and `.SENT` were also subscribed for this probe |

Save the returned IDs to local configuration only. Restart the listener after changing
its IDs. A new Quick Tunnel has a new hostname: retarget the existing subscriptions and
verify them again; do not create extra subscriptions on every run. The tunnel must be
running for delivery. Render's stable URLs belong to the later application deployment.

To test mock events, call `POST /v1/notifications/simulate-event` with the URL ending
in `/simulator/treasury` or `/simulator/vendor` and a matching event type. Only these
separate routes use PayPal's literal `WEBHOOK_ID`; their evidence has `simulated=true`.
Simulator signatures are rejected on real routes. Mock events cannot verify through
PayPal's postback verification endpoint.

For real events, create a new sandbox transaction and confirm the resource with `GET`.
The recorded run used one $1.00 payout to the authenticated vendor's payer ID and then
recorded its confirmed payment against a $1.00 vendor invoice. Notification emails were
disabled. Do not rerun money-moving actions just to redeliver an event: use
`POST /v1/notifications/webhooks-events/{event_id}/resend` with the registered webhook ID.

The JSONL output includes only receipt metadata, not bodies, signatures, emails, tokens,
or credentials. HTTP 204 means signature and certificate-chain verification succeeded;
HTTP 400 rejects invalid evidence, and HTTP 413 rejects a body exceeding 64 KiB.
Repeated deliveries may appear more than once in this evidence log; production deduplication
is P1-09. A delivery older than ten minutes is rejected even on retry.

Certificate downloads use exact HTTPS hosts, bounded responses, and no redirects. The
signing certificate is verified against system trust roots, the expected PayPal signing
hostname, validity dates, and the **measured DigiCert EV RSA CA G2 issuer**. A different
issuer fails closed until this spike is updated and retested. The cache holds at most
16 signing certificates and expires at the earlier of one hour or chain expiry.

See [the measured result](../../../docs/spikes/SPIKE-8.md),
[PayPal's verification guide](https://developer.paypal.com/api/rest/webhooks/rest/),
and [the Webhooks API](https://developer.paypal.com/docs/api/webhooks/v1/).
