# P1 completion boundaries

Remaining foundation implementation is grouped into one review branch. The audit foundation and prior handoff are included; related tests run in a consolidated final pass.

Q4: use the official AG Studio dark theme as the base and share its tokens with the application. This retains sponsor integration, native widgets and existing keyboard/contrast behavior.

Q2: the PRD decided the rule on October 22: HIGH_RISK cases require two distinct eligible humans, neither the uploader. The mocked case view demonstrates this locally; authentication and the production policy gate remain later tasks. Recording the existing decision does not permit bypassing those services.

P1-16 uses the owner-authorized October 27 substitution: three cited public AP/finance case profiles with evidence/inference mapping and source limitations in USER_RESEARCH.md. No firsthand interviews, AegisPay usability results or endorsement are claimed; those remain deferred. The revised task is assessed against the documented secondary-research scope, not the replaced interview requirement.

P1-12 requires real evidence of CLI upload through sandbox settlement. Local tests, seeder manifests, direct settlement fixtures and HTTP 501 case contracts do not satisfy that exit. Independent source reviews on October 27 confirmed that the mounted app has no authenticated document-upload route, case routes return HTTP 501, and the parser, extraction/forensics, matching, risk, policy gate, graph and approval-to-outbox path are absent. The seeder creates trusted records and invoices; it does not implement that pipeline. A test-only approved execution or a hardcoded CLEAN classification would bypass R3/R11/R14/R22 rather than prove S1.

Unblock P1-12 in this order:

1. Implement authenticated sessions and document handling (P3-13, P2-13), isolated PDF parsing (P3-14), and the clean S1 PDF fixture from PRD §15.
2. Connect A2 extraction (P2-01), PDF structure forensics (P2-02), A3 classification (P2-05), trusted-record matching (P2-06), risk (P2-07) and policy gate (P2-09), preserving fail-closed outputs.
3. Connect the durable graph (P2-10) and execution/decision routes (P2-13) to the real approval/outbox boundary; no fixture-created approval or caller-supplied actor, amount or payee. Required human-review paths must retain the PRD R11 approval contract.
4. Configure private sandbox/provider/operator values and run the real CLI upload against the API and separate settlement worker. Configuration is necessary after the engineering dependencies; Docker startup alone cannot supply the missing pipeline.
5. Save execution, invoice and payout IDs plus authoritative GET results: S1 derives **48000 cents** from PO-1042, accepted GRN and the structured invoice, pays the frozen vendor-master payer ID, confirms item SUCCESS, records 48000 cents, confirms invoice PAID or MARKED_AS_PAID, reaches SETTLED and closes PO-1042 (PRD §15, R6/R7/R17).

P1-12 stays unchecked until this complete flow has actual sandbox evidence. No direct payout shortcut is added to manufacture the result.

P1-24 supplies the separate worker executable, process environment checks, database capability checks, local role provisioning and a Render blueprint. A hosted worker is not asserted until the owner configures and deploys the account resources. Render resource/credit evidence and Studio licence/AI entitlement remain owner-dependent.

P2-26 still owns audit queue consumption, production key wiring, external anchors and export verification. P2/P3 own real case decisions, session/CSRF enforcement, live SSE and replay-provider behavior. The P1 case API seam fails closed with HTTP 501 until those services exist.

## Local runtime check — October 27

Docker Desktop is running (engine 29.9.2). The existing public template starts only Postgres and API, with an alternate host database port because Windows denied binding 5432:

```powershell
$env:POSTGRES_PORT='25436'
docker compose --env-file .env.example up --detach --wait --wait-timeout 90 postgres api
```

Migration exited 0; Postgres 16 and API are healthy. `http://127.0.0.1:8000/api/health` returns HTTP 200 with `{"status":"ok","db":true}`. Host PostgreSQL is port 25436; the API uses internal port 5432. GET `/api/executions` returns HTTP 501 because the case service/operator authentication is unimplemented; GET `/api/documents` and `/api/auth/login` return HTTP 404, and the published OpenAPI has no document-upload or authentication routes. These observations confirm the implementation dependency; they do not establish S1.

The `aegispay` project's API/Postgres and persistent `aegispay_pgdata` volume remain running at the user's request. Frontend and settlement were not started, and no provider call occurred. Private sandbox/provider/operator configuration is still needed for a later real run, in addition to the missing engineering pipeline.
