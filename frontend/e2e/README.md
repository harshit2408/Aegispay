# Replay browser checks (P4-04)

From the repository root, install `backend[dev]` in Python 3.12 and run a disposable PostgreSQL 16
server at `localhost:55433` with the synthetic `postgres:postgres` login. In `frontend/`, run:

```text
npm ci
npx playwright install chromium
npm run test:e2e
```

`TEST_PG_URL` may override the PostgreSQL base URL (without a database path). `E2E_PYTHON` may select
the Python executable containing the backend installation. Use a disposable database server: the
fixture requires database-creation privileges and creates/drops only its own `aegis_browser_<uuid>`
database. It never reads private environment files. Ports 3100 and 8100 must be free.

The suite uses Chromium, actual authenticated HTTP routes, CSRF, PostgreSQL, LangGraph checkpoints
and SSE. Provider/extraction results are synthetic replay recordings. The test-only API lifespan
seeds trusted records and installs the same graph used in backend replay checks; it replaces
production role validation, provider setup and recovery startup. The production route handlers,
cookie flags, CORS middleware and browser client are exercised without route interception.
The fixture supplies no settlement worker. Checks assert S1/S2 locked payables, S3 rejection,
S4 original-case linking, the detector-disabled freeze, replay banners and zero spend/PO/outbox rows.

This verifies replay decisions and browser behavior, not live payouts, settlement completion,
provider accuracy or hosted deployment. `/__test__/replay-invariants` exists only in this fixture.
CI runs these checks in its own PostgreSQL service and retains failure traces/screenshots for seven
days. Traces contain only the fixture's synthetic credentials and records.

For isolated Linux API verification from a Windows host, run this fixture in a dedicated container
with the backend and `frontend/e2e/replay_server.py` copied into the same relative layout, publish
port 8100 to localhost, and set `E2E_API_EXTERNAL=1` for Playwright. This skips only API startup;
Playwright still owns its dedicated Next.js server.
