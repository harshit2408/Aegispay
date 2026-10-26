# Spike probes (PRD §18)

Live probes against the PayPal **sandbox**. Throwaway tooling: product code never imports this folder.
SPIKE-9 reads only `SPIKE9_DB_URL` (localhost hosts only; keep the password in `PGPASSWORD`, not the URL) and needs `docker compose up -d postgres`; if a native Postgres already holds host port 5432, map another host port.
The PayPal probes read `.env` at the repo root (git-ignored) and print statuses, ids and timings. They never print secrets.
Each probe exits 0 on pass. Results are recorded in PRD §18; rerun after any change to the sandbox apps.

| Script | Spike | Money moved |
|---|---|---|
| `spike1_invoice.py` | SPIKE-1 vendor creates + sends invoice | none |
| `spike2_treasury_read.py` | SPIKE-2 treasury reads vendor invoice | none |
| `spike3_payout.py` | SPIKE-3 payout status transitions | 1.00 USD sandbox |
| `spike4_record_payment.py` | SPIKE-4 partial then full payment | none (records only) |
| `spike5_unclaimed.py` | SPIKE-5 UNCLAIMED payout + cancel attempt | 1.00 USD sandbox |
| `spike6_toolkit.py` | SPIKE-6 Agent Toolkit in LangGraph, read-only | none |
| `spike10_payer_id.py` | SPIKE-10 vendor payer ID (order `payee.merchant_id`) + payout by `PAYPAL_ID` | 1.00 USD sandbox |
| `spike11_recovery.py` | SPIKE-11 re-POST same `sender_batch_id` (same and new Request-Id) + lookup attempts | 1.00 USD sandbox |
| `spike9_checkpoint.py` | SPIKE-9 `interrupt()` + `AsyncPostgresSaver` across a process restart (local Postgres, no PayPal) | none |

```
python backend/scripts/spikes/spike1_invoice.py        # same for the others
python -m venv .spike6 && .spike6/Scripts/pip install -r backend/scripts/spikes/requirements-spike6.txt
.spike6/Scripts/python backend/scripts/spikes/spike6_toolkit.py
python -m venv .spike9 && .spike9/Scripts/pip install -r backend/scripts/spikes/requirements-spike9.txt
PGPASSWORD=<POSTGRES_PASSWORD from docker-compose.yml> SPIKE9_DB_URL=postgresql://aegis_api@localhost:5432/aegis .spike9/Scripts/python backend/scripts/spikes/spike9_checkpoint.py
python -m pytest backend/tests/unit/test_spikes_common.py   # offline
```

Run them without `python -I` (they import `common.py` from their own folder).
Webhook observations and event shapes are now recorded in SPIKE-8 below. SPIKE-2 remains inconclusive until Invoicing is enabled on the Treasury app; use the VendorGateway fallback. The cross-spike decisions and evidence limits are indexed in `docs/spikes/README.md`.

## SPIKE-8: sandbox webhook verification (`spike8_webhooks.py`)

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
