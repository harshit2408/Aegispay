# S1 runbook: headless happy path against the PayPal sandbox (P1-12)

Goal: PRD §15 S1 with real evidence. A CLI logs in as `demo-controller1`, uploads the clean S1 PDF, the API
parses it in the sandboxed subprocess, the graph matches/risks/gates it to `APPROVED_FOR_PAYMENT`, the separate
settlement worker pays **48000 cents** to the vendor-master payer ID, a `GET` confirms item `SUCCESS`, the invoice
becomes `MARKED_AS_PAID`, the case is `SETTLED` and PO-1042 closes. Nothing here pays, approves or writes the
database on the CLI's behalf (R1, R3, R11). Offline proof of everything up to `APPROVED_FOR_PAYMENT` is
`backend/tests/e2e/test_live_pipeline_offline.py`; this runbook adds PayPal, the LLM and settlement.

All commands are PowerShell, run from the repository root. Nothing below prints a secret; keep it that way.

## 0. Inputs and isolation

* `C:\Users\Admin\Desktop\projects\Aegispay\.env.s1` (git-ignored, outside any worktree) holds the private values.
  Required names are listed by the preflight, never their values: `TREASURY_CLIENT_ID`, `TREASURY_CLIENT_SECRET`,
  `TREASURY_ACCOUNT_EMAIL`, `VENDOR_CLIENT_ID`, `VENDOR_CLIENT_SECRET`, `VENDOR_ACCOUNT_EMAIL`,
  `VENDOR_PAYER_ID` (from the supplier's own sandbox account, SPIKE-10, PRD §18; never from an invoice or PDF),
  `LLM_API_KEY`, `AUDIT_ANCHOR_REPO`, `AUDIT_ANCHOR_TOKEN`, `AUDIT_HMAC_KEY` (>= 32 chars), `SESSION_SECRET`
  (>= 32 chars), `POSTGRES_PASSWORD`, `DEMO_ENABLED=true`, `DEMO_REPLAY_MODE=false`, sandbox `PAYPAL_BASE_URL`.
  Webhook IDs are optional: the reconciler polls, so settlement does not depend on webhooks.
* Compose project `aegispay-s1`, API on 8000, Postgres on 25436. Use a different project name and ports if
  another stack is running. Never run `docker system prune`; clean up with step 9 only.

```powershell
$envFile = 'C:\Users\Admin\Desktop\projects\Aegispay\.env.s1'
# Load the file into THIS process only (compose and the preflight both read the process environment). Prints nothing.
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
    $v = $Matches[2].Trim(); if ($v.StartsWith('#')) { $v = '' }   # `KEY=   # note` is blank
    Set-Item "Env:$($Matches[1])" ($v.Trim('"').Trim("'"))
  }
}
$env:COMPOSE_PROJECT_NAME = 'aegispay-s1'; $env:API_PORT = '8000'; $env:POSTGRES_PORT = '25436'
```

## 1. Preflight (names only; stops here if anything is missing)

```powershell
python backend/scripts/s1_preflight.py --env-file $envFile
```

Exit 1 prints `MISSING NAME: reason` lines. Fix `.env.s1` and rerun. Do not continue on a failure. The LLM key is
sent only to the provider named by `LLM_PROVIDER` (`openai` default, or `openrouter` with the pinned free model; blank
`LLM_MODEL_*` = that provider's reviewed default, see docs/TOOLS.md). The preflight fails on an unreviewed selection.
A value written as `KEY=   # note` is blank, exactly as in `docker compose`.

## 2. Generate operator passwords, Argon2id hashes and the two runtime DB passwords

Runs inside the backend image (argon2 is a backend dependency; the host needs no Python packages). The files are
created with `open(..., "x")`, so an existing file is never overwritten.

```powershell
docker build -t aegispay-s1-tools ./backend
docker run --rm -e VENDOR_PAYER_ID -e VENDOR_ACCOUNT_EMAIL -e TREASURY_ACCOUNT_EMAIL `
  -v "${PWD}/backend/scripts:/scripts:ro" -v "${PWD}:/out" aegispay-s1-tools `
  python /scripts/s1_init_secrets.py --run s1a --out-dir /out `
  --onboarding-reference "SPIKE-10 order id and where the private verification record is kept"
git check-ignore -v operator-seed-s1.json .env.s1.operators s1-out/x   # all three must print a rule
python backend/scripts/s1_preflight.py --env-file $envFile --env-file .env.s1.operators --after-init
```

`operator-seed-s1.json` (identity plus Argon2id hashes) and `.env.s1.operators` (`S1_OPERATOR_PASSWORD_<NAME>` for the
seven named operators, `S1_API_DB_PASSWORD`, `S1_SETTLE_DB_PASSWORD`) are the only places the generated plaintext
exists. Both match `.gitignore`; if `git check-ignore` prints nothing for one, stop and do not commit.

## 3. Bring up Postgres, migrations, API and the separate settlement worker

`docker-compose.s1.yml` layers three different DB passwords over the base file: owner (migrations, seeding),
`aegis_api` and `aegis_settle`, each with only its capability role (R22). The settlement image has git (audit anchor)
and no PDF toolchain; the API image has tesseract (the parser's OCR-diff detector needs `/usr/bin/tesseract`).

```powershell
$c = @('compose','-f','docker-compose.yml','-f','docker-compose.s1.yml','--env-file',$envFile,'--env-file','.env.s1.operators','--profile','settlement','--profile','s1-tools')
docker @c up --detach --build --wait --wait-timeout 240 postgres api settlement
curl.exe -s http://127.0.0.1:8000/api/health        # {"status":"ok","db":true,"demo_enabled":true,"replay_mode":false}
docker @c logs --tail 30 settlement                  # no "Invalid service configuration fields"; audit startup drain done
```

The init script that sets the two runtime passwords runs only on a **fresh** volume. If `aegispay-s1_pgdata` exists
from an earlier attempt, run step 9 first.

## 4. Seed the trust roots and the supplier invoice (run `s1a`)

```powershell
docker @c run --rm --no-deps tools python -m app.demo --config /seed/operator-seed-s1.json `
  --manifest-dir /out/demo-runs --apply-db --send-invoices | Out-Null
$inv = (Get-Content s1-out/demo-runs/s1a.json | ConvertFrom-Json).scenarios.S1.paypal_invoice_id
$inv                                                  # INV2-... sent by the Vendor app, status SENT
```

This writes operators, the vendor-master record (payer ID from `VENDOR_PAYER_ID`), an active demo mandate, PO-1042-s1a
and the SEEDED receiver GRN (100 received, 100 accepted), and sends the $480.00 PayPal invoice
`AP-1042-s1a`. Keep `s1-out/demo-runs/s1a.json`: it journals the PayPal draft ID so a retry resumes with the same
request ID. Use a new `--run` id (and delete nothing) for another attempt.

## 5. Build the clean S1 PDF (PRD §15)

```powershell
docker @c run --rm --no-deps tools python /scripts/s1_make_pdf.py --run s1a --out /out/S1.pdf
```

100 x LiDAR bracket @ $4.80 = $480.00, invoice `AP-1042-s1a`, PO `PO-1042-s1a`, payee text = the Vendor account email.
No hidden text. The PDF's payee is evidence only; the payer ID that gets paid is the frozen vendor-master value.

## 6. Run the headless S1

The `tools` container shares the API's network namespace, so the CLI reaches the API on loopback (it refuses
cleartext HTTP to anything else). The controller password reaches it through the process environment, never argv.

```powershell
docker @c run --rm --no-deps tools python /scripts/s1_headless.py --pdf /out/S1.pdf `
  --po PO-1042-s1a --invoice $inv --out /out/s1-evidence.json --timeout 300
```

* `S1 PASS: evidence saved ...` and exit 0: every PRD §15 check in `verdict()` held on authoritative reads.
* `Needs human approval (R11)` (status `FROZEN`): stop. Approvals need two distinct real operators in the cockpit
  (`demo-controller2`, `demo-cfo`); the CLI never approves. Do not fake it.
* Any `FAIL:` line is a real finding. Triage with the `paypal-debug-id` in `docker @c logs api` /
  `docker @c logs settlement`, not by guessing. Any `[SPIKE-n]` behaviour not recorded in PRD §18 stays a stub.
* `audit anchor not verified` after the grace period: check `AUDIT_ANCHOR_REPO`/`TOKEN` and the settlement logs.

## 7. Collect the DB-side evidence

```powershell
$exec = (Get-Content s1-out/s1-evidence.json | ConvertFrom-Json).execution_id
Get-Content backend/scripts/s1_evidence.sql | docker @c exec -T postgres psql -U aegis_owner -d aegis -At -v "execution_id=$exec" > s1-out/s1-db.json
```

`s1-db.json` carries the payout batch and item IDs, `item_status` (`SUCCESS`), `payout_amount_cents` and
`paypal_amount_cents` (both 48000), `receiver_payer_id` equal to `vendor_master_payer_id`, and `po_status`
(`CLOSED`).

## 8. Save the evidence

Copy `s1-out/s1-evidence.json` (the API case view, including the verified timeline and anchor) and `s1-out/s1-db.json`
into one committed file `docs/evidence/P1-12-s1.json` with: execution ID, PayPal invoice ID, payout batch and item
IDs, statuses, 48000 cents, anchor verdict. IDs and cents only: re-read the file for tokens, emails of real people and
secrets before `git add`.

## 9. Tear down (this project only)

```powershell
docker @c down --volumes        # removes aegispay-s1 containers, network and pgdata; nothing else
docker image rm aegispay-s1-tools
```

Keep `.env.s1.operators`, `operator-seed-s1.json` and `s1-out/` while you still need to log in or retry; delete them
by hand when finished. They are git-ignored and must never be committed.

## Known limits

* `[SPIKE-4]` `MARKED_AS_PAID` and `[SPIKE-10]` payer-ID onboarding are accepted only as recorded in PRD §18.
* Passwords for the four non-controller operators are generated but not used by the CLI.
* The case view asks PayPal for the live invoice status once the payout has succeeded; if that read fails it falls
  back to the match-time status, which fails `verdict()` rather than guessing.
