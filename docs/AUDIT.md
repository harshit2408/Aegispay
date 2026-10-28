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
It refetches once if a competing publisher advanced a receipt between fetch and verification.
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
arrays and a 30-second timeout; transport errors never echo their output or private URLs.

## Private setup

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
An anchor transport outage retries with generic logs; an unavailable remote is never replaced
by a DB-derived checkpoint. Hosted availability and the authenticated export HTTP route remain
separate integration/deployment work.

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
