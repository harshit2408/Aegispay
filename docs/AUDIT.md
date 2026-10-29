---
tags: [aegispay, audit, security]
source: "[[PRD_SRS]] §§4 R21–R23, 8, 13"
---
# Audit runtime and verified export (P2-26)

The settlement worker drains `audit_queue`, signs the single global HMAC chain, and publishes
only checkpoint metadata to the `audit-anchors` Git branch. No invoice text, actor, event
payload, provider credential or HMAC key is published. This implementation is verified with
disposable PostgreSQL and a local bare Git remote; it does not establish hosted publication.

## Transactions and ordering

API and settlement producers call `enqueue_event` inside their existing transaction, with an
authenticated actor and an already-redacted payload. Every successful `transitions.apply`
enqueues its old/new status and row version in the same transaction as the money-state change.
An unsigned queue row is input awaiting signing, not a verified audit event.

Producers never acquire the global chain advisory lock: money operations can acquire mandate
locks before or after enqueuing. Adding the chain lock to producers would invert those orders
and deadlock concurrent reservation/release work. The consumer holds the chain advisory lock
and briefly locks `audit_queue` in `SHARE ROW EXCLUSIVE` mode. That lock waits for existing
INSERT transactions and blocks new ones while it reads a fresh READ COMMITTED snapshot,
signs up to 100 rows in `queue_seq` order and deletes those rows atomically. A failure rolls
back both the signed events and queue deletion. The existing settlement DELETE privilege
permits the table lock; migration `0007` adds INSERT and sequence use for settlement producers,
without granting audit history UPDATE/DELETE.

Each consumer transaction uses a 250 ms lock timeout and a two-second transaction budget.
Contention rolls back that attempt and leaves queue entries available for the next tick.
The signed prefix is loaded once and verified a fixed number of times per batch, then appended
with an internal tail; it is not reread for every queued row. This still verifies the full
history each tick and is intended for hackathon volume; a trusted checkpoint/cache design is
needed before sustained large histories. The transaction budget cannot preempt synchronous
Python verification work.

Signed append uses the existing canonical JSON/HMAC implementation. Sequence gaps caused by
rolled-back transactions are valid; broken links, changed payloads and reordered events are not.
The consumer does not lock money records and holds no DB transaction across Git/network I/O.
Callers must use the queue; do not combine direct `append_event` and unsigned producer work in
one transaction. Direct signed appends belong to the consumer.

## External checkpoints and recovery

The worker verifies existing history before creating Treasury clients. With an anchor repo
configured, it fetches the latest remote checkpoint, verifies that the DB still contains that
authenticated prefix, then verifies the unanchored suffix. Database `audit_anchors` rows are
receipts only. They may reject remote-history rollback but never replace external trust.
If verification sees an apparent rollback, it rolls back the transaction and refetches up to
two more times, after 250 ms and 500 ms, allowing a competing publication to become visible.
Confirmed integrity failure persists `SETTLEMENT_HALT` with `AUDIT_INTEGRITY_FAILED` in a new
transaction and stops the worker. If the database is unavailable, halt persistence can fail;
the worker still stops and logs only a generic failure. Repair does not clear a persisted halt.
An eligible operator must review the incident before resuming settlement.

A checkpoint is due on startup with no checkpoint, every hour, and when a newly signed event
has type `SETTLED`. The next audit tick observes committed settlement events; polling is two
seconds. Each normal, non-force Git push preserves history and rejects a competing publisher.
On retry, the worker refetches and re-verifies. If a push succeeds but saving its DB receipt
fails, the next run adopts the external checkpoint and records the missing receipt.

Git operations use temporary repositories without checking out remote files. Hooks, credential
helpers, inherited Git configuration and redirects are disabled. The child environment contains
only required OS paths/temporary directories and, if configured, the dedicated anchor token.
Provider/session secrets and the HMAC key never enter the Git subprocess. Commands use argument
arrays and share one 15-second deadline per complete read/publication, below the worker's
45-second shutdown grace. Transport errors never echo their output or private URLs. Redirects
remain disabled to protect the dedicated token; update a renamed repository URL explicitly.
Startup and runtime race each audit tick against the stop signal, cancelling and awaiting
the tick on shutdown. A cancelled Git thread retains its operation deadline and owns no DB
transaction; shutdown does not wait through the tick's remaining retries.

## Private setup

### Upgrade an existing Render worker

The Blueprint deliberately has no owner-credential `preDeployCommand`. API and settlement
logins cannot run owner migrations (R22). Deployment is a controlled, manual sequence:

1. Keep settlement halted and pause the existing worker before applying this release. Record
   the maintenance actor and reason. Leave reservations, queued work and audit history intact.
2. From a trusted one-shot migration environment containing the release's source and only
   its private owner `MIGRATION_DATABASE_URL`, run
   `python -m alembic -c backend/alembic.ini upgrade head`. Do not add that URL to Render's
   API or worker environment. Check the migration output and verify the following grants
   for the actual restricted worker login (shown here as `aegis_settle`):

   ```sql
   SELECT has_table_privilege('aegis_settle', 'public.audit_queue', 'INSERT') AS can_enqueue,
          has_sequence_privilege('aegis_settle', 'public.audit_queue_queue_seq_seq', 'USAGE')
              AS can_number_queue;
   ```

   Both results must be true. A revision label alone does not prove these grants exist.
3. Before deploying the new worker, privately configure its stable `AUDIT_HMAC_KEY` of at
   least 32 characters. Preserve the existing signing key if any signed history exists;
   changing it invalidates that history. Configure the anchor repository and dedicated
   token as described below, or explicitly choose HMAC-only mode for a never-anchored chain.
4. Deploy the worker from the reviewed release. Check startup role/configuration validation,
   audit verification, queue draining and the configured anchor policy while settlement
   remains halted. A failed migration, missing key or failed startup is a stop condition;
   do not clear halt or bypass validation to make deployment succeed.
5. After an eligible operator reviews the checks, clear the maintenance halt through the
   authorized operational procedure. Confirm a new transition can enqueue
   and be signed. Keep the audit key and anchor history when rolling application code back;
   do not downgrade grants while this worker release is running.

The authenticated halt HTTP route is not implemented yet. Until it exists, an eligible
`cfo`/`admin` and the database owner must perform the maintenance change from a trusted SQL
console, recording authorization and the before/after results in their operational record.
Replace `operator:VERIFIED_ID` with that operator's identity; no application service gets the
owner URL. To begin maintenance:

```sql
BEGIN;
SELECT * FROM settlement_controls WHERE id = TRUE FOR UPDATE;
UPDATE settlement_controls
SET halted = TRUE, halted_reason = 'MAINTENANCE_P2_26_UPGRADE',
    changed_by = 'operator:VERIFIED_ID', changed_at = now()
WHERE id = TRUE AND halted = FALSE
RETURNING *;
COMMIT;
```

Require one returned row and confirm `halted = TRUE` before pausing the worker. Zero rows
means stop and investigate a missing row or existing halt; never overwrite an incident reason.
After deployment verification, the same eligible operator may authorize resume:

```sql
BEGIN;
SELECT * FROM settlement_controls WHERE id = TRUE FOR UPDATE;
UPDATE settlement_controls
SET halted = FALSE, halted_reason = 'P2_26_UPGRADE_VERIFIED',
    changed_by = 'operator:VERIFIED_ID', changed_at = now()
WHERE id = TRUE AND halted = TRUE
  AND halted_reason = 'MAINTENANCE_P2_26_UPGRADE'
  AND changed_by = 'operator:VERIFIED_ID'
RETURNING *;
COMMIT;
```

Again require one returned row. Zero rows must leave settlement halted pending review; these
commands cannot clear `AUDIT_INTEGRITY_FAILED` or another concurrent incident halt.

Apply migration `0007` using the separate owner/migration login. Keep these variables only in
the settlement service; the API refuses them, including empty values:

| Variable | Required behavior |
|---|---|
| `AUDIT_HMAC_KEY` | Required, at least 32 characters; use a securely generated secret and retain it across restarts. Changing it invalidates verification of existing history. |
| `AUDIT_ANCHOR_REPO` | Credential-free HTTPS URL of a public repository. The branch is fixed to `audit-anchors`. An existing absolute local repository is supported for local verification. |
| `AUDIT_ANCHOR_TOKEN` | Optional dedicated repository-write token for HTTPS publication. Keep it out of URLs, Git, logs and chat. It requires an anchor repo. |

Protect `audit-anchors` against deletion and force pushes. Restrict the publishing credential
to its intended repository. Configure the real remote and token privately before enabling
external anchoring; no production remote or token is supplied by this change.

Without `AUDIT_ANCHOR_REPO`, the PRD's explicitly permitted degraded HMAC-only mode logs
`audit_external_anchor_disabled mode=HMAC_ONLY exports=unavailable`. Signed events still drain,
but externally verified exports refuse to run. A missing/short HMAC key refuses worker startup.
Removing the repo after external receipts exist is refused as lost external history, rather
than silently downgrading a previously anchored chain.
A configured anchor transport outage logs a generic stop-worker error and stops settlement
both at startup and during normal operation. It does not persist an integrity halt merely for
a transport failure. Restart only recovers after startup verifies the full chain and remote
checkpoint again. A normal competing-publisher conflict retries on the next tick. An unavailable
remote is never replaced by a DB-derived checkpoint. Hosted availability and the authenticated
export HTTP route remain separate integration/deployment work.

## Export

Run inside the settlement environment, without copying its signing key into the API:

```text
python -m app.audit.export EXECUTION_ID
python -m app.audit.export EXECUTION_ID --format csv
python -m app.audit.export EXECUTION_ID --include-replay
```

Export fetches the latest external checkpoint and verifies the complete global chain before
filtering to the requested execution. It exports only anchored events and reports
`pending_unanchored_events` for the authenticated suffix. Replayed executions are excluded
unless `--include-replay` is explicit. No external checkpoint, tampering or missing anchored
history causes a generic refusal and nonzero exit, with no credential-bearing traceback.

JSON contains `anchor`, `events` and `pending_unanchored_events`. CSV repeats checkpoint
metadata on each event row and escapes spreadsheet formula prefixes. An empty CSV export
contains one metadata-only row with blank event fields, so its checkpoint and pending count
are not lost. Exported event subsets are verified by this settlement operation against the
global chain; a subset alone cannot independently reconstruct unrelated events' HMACs.

The API export endpoint and case-view download remain disabled until authenticated routing
and a settlement-side verification seam are implemented. Tests do not establish that endpoint,
live sandbox S1–S4, public Git branch protection or hosted credential isolation.
