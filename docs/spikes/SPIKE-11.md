# SPIKE-11 — payout recovery evidence and closeout

P0-20; PRD §18, R5/R19. Dev A's sandbox run is recorded in PRD §18 (2026-10-23)
and [PR #2](https://github.com/harshit2408/Aegispay/pull/2). Dev B reviewed and hardened
the probe on 2026-10-26. No PayPal request was made during this closeout.

## Recorded sandbox result

- Initial 1.00 USD `EMAIL` payout returned HTTP 201.
- Re-POSTs with the same body and either the same or a new Request-Id returned HTTP 400,
  `USER_BUSINESS_ERROR`. Request-Id did not replay the original HTTP 201.
- The `SENDER_BATCH_ID` detail carried a `link[]` entry (`rel=self`, `method=GET`) with
  `https://api.sandbox.paypal.com/v1/payments/payouts/<existing payout_batch_id>`.
  The recorded run created no second batch: 1.00 USD moved in total.
- List-by-sender-ID and list payouts returned HTTP 404. Transaction Search returned
  HTTP 403 `NOT_AUTHORIZED`; the Treasury app lacked reporting scope.

**Go:** the measured duplicate error exposes the existing ID. Adopt only after GET on
the configured sandbox base verifies the original frozen single-item command. **No-go:**
missing/malformed recovery information, another batch, or a command mismatch. Product
settlement must keep reservations and enter `NEEDS_MANUAL_REVIEW` under R19.

## Offline verification, 2026-10-26

The prior second-batch fix is retained. Five new regressions first failed against PR #2's
published code: an ID mentioned without a link, and receiver, amount, recipient-type or
sender-item mismatches. The hardened probe requires an exact sandbox HTTPS link shape,
extracts the ID without following the URL, and GET-confirms batch ID, sender batch ID,
single item, sender item ID, recipient type, receiver, amount and currency.

The SPIKE-11 test suite passes 25 cases, including both allowed sandbox origins, malformed
error shapes, live/lookalike/userinfo/port/query/fragment links, extra items and wrong headers.
These are offline mocked regressions, not another sandbox run or a production recovery test.

## Limits

The real duplicate-error shape remains unconfirmed for `PAYPAL_ID`; SPIKE-10's successful
`PAYPAL_ID` payout does not establish that error shape. Uniqueness retention duration also
remains unmeasured, so the PRD's bounded recovery TTL and fail-closed outcome still apply.
No worker crash, outbox/ledger adoption, halt, TTL or R20 recheck implementation is claimed.
