---
tags: [aegispay, setup, render]
source: "[[PRD_SRS]] §§7, 16, 19, 22; R22; ADR-007"
---
# Render setup (P0-04)

P0-04 requires an actual project, available Postgres instance and redeemed credits.
This guide prepares those actions; it is not provisioning evidence. Checklist state stays in
[[CHECKLIST]]. Full application deployment and `render.yaml` belong to P3-04.

## Account and credits — owner action

On 2026-10-25 the inspected browser reached Render's **Sign In to Render** page at
<https://dashboard.render.com/login>. No account resources or billing balance were visible.
The user is handling account setup; no resources, paid plans or billing settings were changed.

1. Sign in to the [Render Dashboard](https://dashboard.render.com/) and select the workspace
   that will own AegisPay.
2. Open the [official hackathon Render page](https://paypalaihackathon.devpost.com/details/render).
   Its $50 participant-credit link leads to the
   [hackathon claim portal](https://credits-portal-mmdm.onrender.com/claim/paypal-ai-hackathon).
   Complete the claim there; keep any issued promo code out of Git and chat.
3. In that same workspace, open **Billing → Credit Balance**, enter the issued code and select
   **Apply**. Check that the balance reflects redemption. Record the redemption date, amount
   and promotion expiry without exposing billing details. Credits cannot move between
   workspaces; adding them does not upgrade a plan. See
   [Render's redemption instructions](https://render.com/docs/credits).
4. Check the credit expiry against **2026-12-15**. The promotion's expiry is unverified;
   ask the sponsor through the official page if the claim does not explain it.
   Credits offset eligible bills at month end; excess usage can charge the payment method.
   Review the displayed resource cost and coverage before creating a paid instance.

## Project and database

These are proposed settings, not observed resource values. Reuse an existing suitable project
or database rather than creating duplicates.

| Setting | Proposed value / constraint |
|---|---|
| Project | `AegisPay` |
| First environment | `sandbox` (PayPal sandbox; this label grants no security controls) |
| Postgres service name | `aegispay-postgres` |
| Database name | `aegis` |
| PostgreSQL version | **16**, matching the repository's local Compose image |
| Region | **Singapore**, proposed for the developers in India; keep API, settlement worker and database together |
| Compute / storage | Select after credit and displayed-price review; record the actual plan and storage |

Create the project with **New → Project**, providing its name and initial environment.
Then use **New → Postgres** ([creation form](https://dashboard.render.com/new/database)),
assign it to that project/environment and enter the settings above. Wait for **Available**.
[Project instructions](https://render.com/docs/projects) and
[database instructions](https://render.com/docs/postgresql-creating-connecting).

Singapore is a supported region. Render cannot change an existing resource's region in place;
cross-region resources lose private-network connectivity. Confirm the region before creating
the database. [Region reference](https://render.com/docs/regions).

Free Postgres expires **30 days after creation**, with no backups. A free database created on
October 25 would expire on November 24, before judging December 1–15. Free web services also
sleep after 15 minutes without traffic, and workers have no free instance option.
PRD §16/§22 require non-sleeping paid instances funded by credits through December 15;
verify the full API + worker + frontend + database budget at P3-04. A temporary Free database
needs an explicit upgrade plan; it is not the final judging configuration.
[Free-instance limits](https://render.com/docs/free).

## Connection and deployment boundary

Use the database's **internal** host from Render services in the same workspace and region.
Disable external database access unless required for setup; if temporarily enabled, restrict
it to the operator's IP and require TLS. Never put connection URLs/passwords in this document.
[Connection and access settings](https://render.com/docs/postgresql-creating-connecting).

The generated owner credential is for administration, not the application. P1-19/P1-24 must
create the separate `aegis_api` and `aegis_settle` roles and verify grants before deployment.
Use `postgresql+asyncpg://` for SQLAlchemy's `DATABASE_URL` and
`SETTLEMENT_DATABASE_URL`; the API's `CHECKPOINT_DB_URL` uses `postgresql://` with the API
role. Driver-specific TLS settings must be verified in the deployment connection test.

Supply only each service's PRD §7 variables. Do not attach a shared group containing all
secrets: Treasury credentials and `AUDIT_HMAC_KEY` belong only to settlement; the LLM key
and session secret belong only to API; frontend receives its public build variables.
The parser subprocess gets an empty environment. Existing skeleton code does not yet
implement the full R22 boot/grant assertions; this guide does not claim that isolation works.

Before P3-04, implement the settlement worker and frontend, service env assertions and grants;
then produce `render.yaml` with one API instance, one settlement worker and the frontend.
Use `/api/health` for readiness and the external uptime check. Keep PayPal sandbox-only and
enable demo routes only under R24's sandbox guard.

When the public API hostname is known, retarget **both sandbox apps'** webhook registrations
to `/api/webhooks/paypal/treasury` and `/api/webhooks/paypal/vendor` (PRD §13).
Recheck webhook IDs for those exact registrations and
repeat real/simulated verification evidence (P0-14/P1-09). A temporary tunnel URL is not the
hosted callback. Set frontend API origin at build time, limit backend CORS to the exact
frontend origin and verify session/CSRF settings before a hosted demo.

## Evidence to finish P0-04

Append only non-secret facts after account access and creation are confirmed:

| Evidence | Verified value |
|---|---|
| Workspace and project resource links | Pending owner setup |
| Environment / actual region | Pending owner setup |
| Postgres resource link, status and version | Pending owner setup |
| Actual compute plan and storage | Pending owner cost review |
| Credit amount, redemption date, expiry | Pending owner redemption |

Mark P0-04 complete only when the project/database and redeemed credit balance are evidenced.
Local tests or a Blueprint alone cannot satisfy that item.
