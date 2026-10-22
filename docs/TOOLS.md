---
tags: [aegispay, tools]
source: "[[PRD_SRS]] §1 note 5, §21"
---
# Tools disclosure (submission requirement)

## APIMatic Context Plugin for PayPal (P0-05)
- [ ] Participant subscription claimed (date: ____)
- Install (from the APIMatic/PayPal getting-started guide): `npx context-plugins install https://github.com/paypaldev/server-sdk-context-plugin-preview` (repo `paypaldev/server-sdk-context-plugin-preview`), then prompt the agent to build with PayPal.
- [x] Plugin installed (Dev A, 2026-10-22, user scope): `~/.context-plugins/installed.json` lists `paypal` v0.1.0, targets claude/cursor/vscode; enabled as `paypal@context-plugins-local` in `~/.claude/settings.json`.
- Coverage (checked 2026-10-22): bundled skills teach the **PayPal Server SDK** (`paypal-server-sdk` 2.4.0; Python skills: auth, client init, calling endpoints, models, error handling, resilience, testing). That SDK has controllers for OAuth, Orders, Payments, Subscriptions, Transaction Search, Vault. It has **no Payouts or Invoicing controller**, so the Treasury and Vendor clients (PRD §9) are hand-written `httpx` and are not plugin-generated.
- Honest use: log only code the plugin really informed (e.g. OAuth token handling, error handling, Transaction Search for the `list_transactions` read). Do not claim Payouts/Invoicing code.
- Not yet verified: that a Claude session loads the plugin skills (needs a restart; check `/skills`).
- Usage log (append one line each time it generates PayPal client code): date · dev · what was generated

## AI models + IDs
Pending P0-16.

## PayPal APIs / Agent Toolkit / AG Studio / Render
Pending (P5-02).
