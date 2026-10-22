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
- [ ] Sandbox Business account A (treasury). Email → `TREASURY_ACCOUNT_EMAIL`
- [ ] Sandbox Business account B (supplier). Email → `VENDOR_ACCOUNT_EMAIL` (MUST equal `invoicer.email_address`)
- [ ] REST app `aegispay-treasury` created under account A → `TREASURY_CLIENT_ID`, `TREASURY_CLIENT_SECRET`
- [ ] REST app `aegispay-vendor-sim` created under account B → `VENDOR_CLIENT_ID`, `VENDOR_CLIENT_SECRET`
- [ ] Enable Payouts on the treasury app; Invoicing on the vendor app (confirm in dashboard; record result in PRD §18 SPIKE-1/3)
- [ ] Webhooks per app (P0-14 / SPIKE-8) → `TREASURY_WEBHOOK_ID`, `VENDOR_WEBHOOK_ID`
- [ ] Put all values in local `.env` only (git-ignored). `PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`

Dashboard click-paths and permission names are not recorded here until confirmed by a spike. Do not fill them in from memory.
