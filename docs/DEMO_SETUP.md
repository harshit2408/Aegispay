# Sandbox demo seeder

`python -m app.demo` prepares P1 trust roots and Vendor simulator invoices. It does not upload
a PDF, run the graph, approve an execution or submit a payout. Headless S1 still requires an
actual settlement run and PayPal confirmation; an offline seed is not that evidence.

Run from `backend/` in the trusted setup environment after applying Alembic migrations with the
database owner. Export `DATABASE_URL` for the owner connection, `DEMO_ENABLED=true`,
`DEMO_REPLAY_MODE=false`, and the sandbox `PAYPAL_BASE_URL`. The CLI reads exported settings;
it does not load secret files. Runtime API and settlement logins cannot create these trust roots.

Prepare a private, ignored `operator-seed-demo.json` file with these fields:

```json
{
  "run_id": "rehearsal1",
  "vendor_payer_id": "<13-character verified supplier payer ID>",
  "vendor_email": "<supplier sandbox account email>",
  "treasury_email": "<treasury sandbox account email>",
  "onboarding_evidence": {
    "method": "vendor-token unapproved order payee.merchant_id (SPIKE-10)",
    "reference": "<order ID and private verification record>"
  },
  "operator_hashes": {
    "controller1": "<Argon2id hash>",
    "controller2": "<Argon2id hash>",
    "cfo": "<Argon2id hash>",
    "receiver": "<Argon2id hash>",
    "vendor_admin1": "<Argon2id hash>",
    "vendor_admin2": "<Argon2id hash>",
    "admin": "<Argon2id hash>"
  }
}
```

Obtain the payer ID independently from the supplier's own sandbox account using the recorded
SPIKE-10 method in `PRD_SRS.md` §18. Never copy it from an invoice, PDF or inbound request.
Generate Argon2id password hashes outside the seeder; it accepts no plaintext passwords and
publishes no judge credentials. Each account represents a distinct named operator. Authentication
and session routes are separate work.

```powershell
# Default: validate inputs and emit the deterministic manifest; no DB or PayPal writes.
python -m app.demo --config ../operator-seed-demo.json
# Commit operators, approved vendor history, a mandate, three POs and SEEDED receiver GRNs.
python -m app.demo --config ../operator-seed-demo.json --apply-db
# Explicit supplier invoice writes; provide Vendor credentials in the trusted setup process.
python -m app.demo --config ../operator-seed-demo.json --apply-db --send-invoices
```

`run_id` is 1–12 lowercase letters, digits or hyphens. POs are `PO-1042-<run>` through
`PO-1044-<run>`; invoice numbers carry the same suffix. S1 is 48,000 cents; S2 bills
50,000 cents with 42,500 accepted. `--send-invoices` creates and sends S1, S2 and S3 invoices; S3's
is only a reference invoice because admission GET-verifies one (PRD §15 S3 variant, `docs/EVAL.md`). GRNs are recorded by
`demo-receiver`; POs by `demo-admin`. Upload as `demo-controller1`, approve as
`demo-controller2` and `demo-cfo`. Vendor history has distinct vendor administrators and an
already-elapsed 72-hour cooling period, with onboarding 90 days earlier.

Reusing the run preserves existing DB rows. The `.demo-runs/<run>.json` journal saves each
PayPal draft ID before sending. Keep it for retries and run one setup process per run. A failed
send resumes the saved draft with the same request ID. Do not delete the journal and retry an
old run after PayPal's idempotency retention window. A fresh suffix creates new POs/GRNs and a
new active demo mandate, preserves prior records, and refuses to replace a non-demo mandate.
A differing vendor identity fails closed and requires vendor change control. PDFs, execution
archival and the authenticated reset endpoint remain outside this P1 seeder.

## Live reset (`POST /api/demo/reset`, `DEMO_ENABLED=true`, replay off)

The API database role cannot create POs, GRNs, mandates or vendor identity (R15, R16, R22), so the
endpoint never seeds anything itself. An admin's request records a `DEMO_RESET_REQUEST` control
event and waits up to 25 s for the owner-side runner, which holds the owner `DATABASE_URL` and the
Vendor credential and reuses this seeder:

```powershell
# One long-lived process per deployment, in the trusted setup environment (never the API process).
python -m app.demo --config ../operator-seed-demo.json --apply-db --send-invoices --serve-resets
# Handle at most one pending request and exit (rehearsal / cron):
python -m app.demo --config ../operator-seed-demo.json --apply-db --send-invoices --serve-resets --once
```

For each request the runner takes the seed file as a template, replaces `run_id` with a fresh
12-character suffix, seeds a new run (POs, `SEEDED` receiver GRNs, new active demo mandate, S1–S3
Vendor invoices) and records `DEMO_RESET_DONE` or `DEMO_RESET_FAILED`. Responses: `200` with the
new `run_id` and `previous_run_id`; `504` while the runner has not finished (repeat the request: it
waits on the same pending request); `502` when seeding failed (only the error class is recorded);
`429` after 10 control requests per minute per session. With no runner the request stays pending
and every call returns `504`.

"Archived" is derived, not stored: once a newer demo mandate is active, finished cases of earlier
runs (`SETTLED`, `REJECTED`, `DUPLICATE_BLOCKED`) leave the `GET /api/executions` grid. Cases still
in flight or needing review stay visible, and no execution row or audit event is changed.

## Live toolbar: detector and scenarios (`DEMO_ENABLED=true`, replay off)

After sending invoices the seeder records a `DEMO_INVOICES` control event (`run_id` and the S1–S3
invoice IDs) in the audit queue. The `.demo-runs` journal stays the PayPal idempotency record; the
API reads the IDs from the database and holds only the Vendor read credential (R22).

- `GET /api/demo/forensics-detector` (admin) returns the state for the active run. `POST` records a
  `DEMO_LIVE_DETECTOR` event bound to that run; a reset seeds a new run, so the detector is ON again.
  OFF applies to every new upload while `DEMO_ENABLED`, not only toolbar scenarios: controller
  uploads through `POST /api/documents` record it too, until the next reset. The analyze node honours
  the recorded state. Without `DEMO_ENABLED` both admission and analysis force the detector ON.
  Latest state wins by audit sequence, not timestamp.
- `POST /api/demo/scenarios/{S1..S4}/run` (admin) builds the run's reportlab PDF and sends it with
  the seeded invoice through the same admission code as `POST /api/documents` (GET-verify,
  duplicate checks, 10/min per session). S3 is the look-alike payee with hidden text; S4 re-uploads
  S1's PDF and invoice, so it is `DUPLICATE_BLOCKED` (409 until S1 has run). Each attempt (failed ones included) records `DEMO_RUN` and counts toward the 10/min demo limit. For the kill-switch
  re-run, reject the first S3 case, turn the detector OFF and run S3 again. A run seeded without
  invoices returns 409.

`GET /api/health` is public and returns only booleans and ages. `200` with `status` `ok` or
`degraded`; `503` only when the database is unreachable. Settlement reports the Treasury and Vendor
OAuth tokens, both webhook registrations (`TREASURY_WEBHOOK_ID`, `VENDOR_WEBHOOK_ID` must be listed
by that app) and Treasury/Vendor account distinctness (R12) every 30 s into `service_health`; the
API adds halt state and last-anchor age. A report older than 90 s reads as unknown. Unset webhook
IDs count as unregistered, so a local stack without public webhooks reports `degraded`.

Migration `0006` creates password-free `aegis_api_role` and `aegis_settlement_role` capability
roles. Provision separate `NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS` login accounts,
grant exactly one capability to each, and keep ownership/migration credentials out of both
runtime processes. No runtime role can edit vendor identity, onboarding or mandate policies.
The future vendor-change route must apply an approved effective change through a constrained
path before any runtime payee-write grant is added. Capability roles never carry deployment
passwords; PostgreSQL `SET ROLE` tests exercise their actual grants.
