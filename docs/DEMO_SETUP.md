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
50,000 cents with 42,500 accepted; S3 deliberately has no PayPal invoice. GRNs are recorded by
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

Migration `0006` creates password-free `aegis_api_role` and `aegis_settlement_role` capability
roles. Provision separate `NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS` login accounts,
grant exactly one capability to each, and keep ownership/migration credentials out of both
runtime processes. No runtime role can edit vendor identity, onboarding or mandate policies.
The future vendor-change route must apply an approved effective change through a constrained
path before any runtime payee-write grant is added. Capability roles never carry deployment
passwords; PostgreSQL `SET ROLE` tests exercise their actual grants.
