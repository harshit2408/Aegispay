---
tags: [aegispay, setup]
source: "[[PRD_SRS]] §3, §7"
---
# Sandbox setup (P0-06)

Two distinct sandbox **Business** accounts and two REST apps (R12: treasury ≠ supplier, or payouts fail with `CANNOT_PAY_SELF`).
Create them in the PayPal Developer Dashboard (sandbox). Tick each box when done; never paste secrets here.

| Actor | Business account | REST app | Env prefix |
|---|---|---|---|
| Treasury (buyer) | `treasury@…` | `aegispay-treasury` | `TREASURY_` |
| Supplier (Vendor Simulator) | `supplier@…` | `aegispay-vendor-sim` | `VENDOR_` |

## Checklist
- [x] Sandbox Business account A (treasury). Email → `TREASURY_ACCOUNT_EMAIL`
- [x] Sandbox Business account B (supplier). Email → `VENDOR_ACCOUNT_EMAIL` (MUST equal `invoicer.email_address`)
- [x] REST app `aegispay-treasury` created under account A → `TREASURY_CLIENT_ID`, `TREASURY_CLIENT_SECRET`
- [x] REST app `aegispay-vendor-sim` created under account B → `VENDOR_CLIENT_ID`, `VENDOR_CLIENT_SECRET`
- [x] Enable Payouts on the treasury app; Invoicing on the vendor app (confirm in dashboard; record result in PRD §18 SPIKE-1/3) — verified by SPIKE-1/3, not before
- [x] Webhooks per app (P0-14 / SPIKE-8) → `TREASURY_WEBHOOK_ID`, `VENDOR_WEBHOOK_ID` — registered and saved locally 2026-10-25; both real/simulator signatures and signing-certificate chains verified. Temporary HTTPS tunnel; retarget existing subscriptions when the tunnel changes or Render is ready. See [evidence](spikes/SPIKE-8.md).
- [x] Put all values in local `.env` only (git-ignored). `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`

Dashboard click-paths and permission names are not recorded here until confirmed by a spike. Do not fill them in from memory.

## Service-specific configuration

Use [.env.example](../.env.example) as the variable inventory. Store values only in the intended service's private configuration. Do not forward the combined template or owner database URL to an application process.

| Process | Private values / responsibility |
|---|---|
| API | Restricted `DATABASE_URL`/`CHECKPOINT_DB_URL`, `SESSION_SECRET`, LLM key/model settings, Vendor credentials for its read adapter, both webhook IDs, `FRONTEND_ORIGIN` and persistent document storage. No Treasury credentials or audit HMAC/anchor token. |
| Settlement | Restricted `SETTLEMENT_DATABASE_URL`, Treasury credentials, Vendor credentials for reconciliation after payout success, audit HMAC key, optional anchor repository/token and alert webhook. No LLM key, session secret or migration owner URL. |
| Migration administrator | `MIGRATION_DATABASE_URL` in a separate owner-controlled migration/grants session only. |
| Frontend build | Public HTTPS `NEXT_PUBLIC_API_URL` and AG Studio licence key supplied privately in Render build configuration. No PayPal/LLM credentials. |

Keep `PAYPAL_BASE_URL` on an approved sandbox origin. Demo/replay refuses a live PayPal origin. Normal API startup validates restricted database privileges. Local Compose provisions separate owner/API/settlement logins; hosted roles/upgrades require the owner procedure in [RENDER_SETUP](RENDER_SETUP.md). Configure same-site HTTPS frontend/API hosts for Strict cookies; restrict CORS to the frontend origin.

## Operators and acceptance

The [README replay quickstart](../README.md#judge-quickstart-local-synthetic-replay) provisions disposable synthetic role accounts without private configuration or PayPal calls. Never deploy its public test passwords. Real roles live in `operators` with Argon2id hashes. The existing `seed_demo` callable in `backend/app/demo/seeder.py` validates seven role hashes and trusted supplier onboarding evidence. Its owner CLI is `python -m app.demo --config <private-seed.json> --apply-db`; omitting `--apply-db` performs a dry run. Follow [S1_RUNBOOK](S1_RUNBOOK.md) for private seed generation and setup. There is no automatic `OPERATOR_SEED_FILE` loader; that template variable alone does not provision users.

Before a hosted sandbox run, the owner must provision private role access and trusted supplier/PO/GRN/mandate records, retarget both webhook subscriptions to the deployed HTTPS treasury/vendor endpoints, and verify the restricted-login API plus separate worker. Follow [AUDIT](AUDIT.md) for HMAC/anchor setup. Public health alone does not prove token, account, webhook, worker or external-anchor readiness.

The 2026-11-12 real sandbox acceptance is recorded in [S1 evidence](evidence/P1-12-s1.json) and [S2–S4 evidence](evidence/P2-15-s2-s3-s4.json), including execution/invoice/payout identifiers and authoritative state. S1 settled 48,000 cents with invoice `MARKED_AS_PAID`; S2 paid 42,500 cents with invoice `PARTIALLY_PAID`. S3 froze and was rejected with zero paid; S4 blocked the duplicate in 41–43 ms. Synthetic replay leaves spend/PO ledgers and outbox empty and cannot establish these payment results.

These individual runs used a free OpenRouter model. S1/S2 preceded the final output-cap and classifier-prompt fixes; S3 used a reference-invoice variant. The detector-OFF and two-human approval paths were not run live, the notice/accept/contest flow remains absent, and held-out model accuracy and hosted acceptance remain unproved. [EVAL](EVAL.md) records the measured outcomes and limits; [CHECKLIST](CHECKLIST.md) remains task state. Hosted URL/uptime evidence, final public video and submission remain separate work.
