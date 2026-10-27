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

### Empty configuration slots and owner inputs

`.env.example` keeps working local database/API URLs and includes a blank hosted-override
worksheet using the actual `render.yaml` variable names. Its credential and AG licence
slots are empty; the administrator-only `MIGRATION_DATABASE_URL` slot is commented out.
Copy only the variables
for each process into that Render service; replace local URLs rather than adding duplicate
keys. Do not import the combined template as one shared environment group.

| Where to configure | Values the owner must supply privately |
|---|---|
| Separate migration session | `MIGRATION_DATABASE_URL` using the database owner; never a runtime service variable |
| API | `DATABASE_URL` and `CHECKPOINT_DB_URL` using the restricted API login; existing Vendor sandbox/webhook values and `LLM_API_KEY`; Render generates `SESSION_SECRET` |
| Settlement worker | `SETTLEMENT_DATABASE_URL` using its restricted login; existing Treasury/Vendor sandbox credentials, account emails and webhook IDs; `AUDIT_HMAC_KEY` |
| Frontend build | `NEXT_PUBLIC_API_URL` with the public HTTPS API origin; `NEXT_PUBLIC_AG_LICENSE_KEY` with the legitimate Studio licence |

`AUDIT_ANCHOR_REPO` and `ALERT_WEBHOOK_URL` already have blank slots; their consumers remain
P2 work. This deployment does not consume a Render API token or a separate AG Studio AI key.
Do not paste licence keys, database URLs or passwords into chat or this document.

To verify the account-dependent work, share only project/Postgres/service dashboard links,
the selected region and plans, redeemed credit balance and expiry, and confirmation that
the Studio licence includes the needed AI entitlement through **2026-12-15**. Confirm when
the private values above are entered, so a licensed rebuild and hosted isolation checks can run.

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
Parser subprocess isolation remains P3-14. P1 supplies API/settlement environment assertions,
actual database-role checks and migration 0006 capability grants. Local evidence does not
establish that the owner has configured those boundaries in the hosted account.

The committed `render.yaml` defines one API instance, one settlement worker and the frontend.
Its resource fields follow the [Render Blueprint reference](https://render.com/docs/blueprint-spec).
Automatic deploys are disabled in the template. Before activating it, provision the runtime
logins and service variables below; migration credentials never belong in a runtime service.
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

## P1-19/P1-24 role and process setup

Run Alembic migrations in a separate administrator session with a database owner that can
create roles. The migrations create `aegis_api_role` and `aegis_settlement_role` as NOLOGIN
capabilities. Their names differ from login names so an existing local owner cannot silently
be mistaken for an unprivileged capability. Runtime logins must be NOSUPERUSER, NOCREATEDB,
NOCREATEROLE and NOBYPASSRLS, and inherit exactly one of those capabilities.

For a new managed database, the owner creates private `aegis_api` and `aegis_settle` logins,
grants only `aegis_api_role` to the former and `aegis_settlement_role` to the latter, and sets
passwords privately. Then set API DATABASE_URL/CHECKPOINT_DB_URL to the API login and worker
SETTLEMENT_DATABASE_URL to the settlement login. Do not reuse the database owner connection.
Boot checks inspect PostgreSQL role flags and memberships, not just the URL username.
Verify the hosted owner's role-management permissions before applying migrations; no hosted
role provisioning is claimed by this repository change.

On a **fresh local Compose volume**, local-db-init.sql supplies clearly development-only
logins, and the one-shot migrate service applies Alembic before the API starts. The API and
optional settlement service then use different, restricted logins. Existing volumes from the
earlier superuser skeleton need an explicit owner-led migration of roles; do not delete a
volume or expect init scripts to rerun automatically. Use a unique COMPOSE_PROJECT_NAME for
disposable verification so another developer's data is preserved.

The settlement profile starts `python -m app.settlement.worker`, with independent outbox,
GET reconciliation and verified-webhook-consumer loops. It requires configured Treasury and
Vendor sandbox credentials. It receives no LLM/session/demo/API database variables. The API
receives no Treasury credentials, signing key or worker database URL. Do not start either
process by loading one combined secret file; inject only its tagged PRD §7 variables.
Settings validation errors expose field names, not credential values. SIGTERM stops the
worker loops and closes HTTP clients and the database engine; leased commands survive a kill.

P1-24 code/configuration can be reviewed locally. Hosted deployment evidence, webhook
retargeting, licence entitlement, uptime and real sandbox S1 still require owner setup and
their explicit acceptance checks. Audit queue consumption/external anchors and the velocity
breaker remain P2 work; worker presence alone does not claim those controls are deployed.
