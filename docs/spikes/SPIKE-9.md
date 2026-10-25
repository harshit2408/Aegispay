# SPIKE-9 — durable interrupt and acceptance receipt

P0-15, PRD §18 / ADR-005. Independent Dev B verification on **2026-10-26 IST: PASS**.

The probe is `backend/scripts/spikes/spike9_checkpoint.py`; its dependencies remain separately pinned in `requirements-spike9.txt`. This is a minimal graph, not the product approval service.

## Observed run

- Windows, Python 3.14.0, native Postgres 18.6, isolated temporary cluster listening only on `127.0.0.1:55441`.
- `langgraph` 1.2.16, `langgraph-checkpoint` 4.3.0, `langgraph-checkpoint-postgres` 3.2.0, `psycopg` 3.3.6 and `psycopg-pool` 3.3.3.
- Thread `spike9-a3d97852-1260-47e7-829c-b9c1a14a3e18`; process IDs 29928, 32292 and 33868.
- Process 1 reached the dynamic interrupt and called `os._exit(0)` inside the saver context, without graceful cleanup.
- Process 2 opened a fresh saver and restored `next=('review',)`, the interrupt payload and `payable_cents=1234`. It resumed using `Command(resume='appr_spike9')` with synchronous durability, completed, then hard-exited before any application consumption step.
- Process 3 opened another fresh saver, restored the persisted acceptance receipt and an empty `next`, and sent no second `Command`.
- Exact database effect counts: `prepare/run` once in process 1; `review/before_interrupt` once in each of processes 1 and 2; `review/after_interrupt` and `finish/run` once in process 2. No extra effects in process 3. The orchestrator printed `PASS` and returned 0.
- The temporary Postgres server was stopped after the run. No application database, PayPal account or secret file was used.

The interrupted node re-runs from its beginning; completed nodes do not. Product nodes must therefore keep pre-interrupt code free of side effects and make later application writes idempotent. The probe deliberately writes effect rows to measure replay.

## Approval contract correction

PRD §8 / §11.3 now require serialized dispatch, an approval bound to the server-read pending checkpoint and interrupt, validation before and inside the node, a durable acceptance receipt, and consumption only after that receipt is read back. Application rows and saver writes use separate transactions; the document does not promise atomicity between them. Recovery must inspect the bound task and receipt, reject ambiguous lineage, and never send an old approval to a later interrupt. A consumed row remains a vote for policy-gate counting, subject to eligibility, gate hash and voiding checks.

This resolves the former contradiction where the router consumed a row before the node required `consumed_at IS NULL`.

## Offline checks and limits

The spike entry-point tests reject explicit nonlocal `hostaddr`, `PGHOST`, `PGHOSTADDR`, service-file overrides and mixed host lists before a database connection. Invalid synthetic resume payloads fail before the acceptance effect; a valid payload returns a receipt. These checks do not implement real approval lookup or authentication.

Not proved here: a Render restart, concurrent product dispatch, lock-loss fencing, approval database validation, every app/saver commit gap, dual-human authorization or payment idempotency. Those remain P1/P2 implementation and §17 acceptance tests. The existing four Postgres 16 runs remain historical evidence in PRD §18; this independent run adds a third-process receipt check on Postgres 18.
