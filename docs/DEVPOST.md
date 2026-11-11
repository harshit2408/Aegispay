# AegisPay — submission text

Prepared for P5-07. This is submission copy, not evidence that the project has been submitted.
Sponsor prize: **Best Use of AG Grid** only.

## Tagline

Invoice-reading agents with payment controls enforced outside the model.

## Problem and intended user

A small finance team needs to review invoices, compare them with purchase orders and accepted
deliveries, resolve short shipments, and avoid paying the same invoice twice. An invoice-reading
agent can reduce repetitive review, but supplier documents and invoice memos are untrusted input.
Their instructions must never choose the receiver or increase an approved payment.

Our primary user is an accounts-payable controller. A CFO owns payment limits and exceptional
approval; a receiver records accepted goods. These roles remain separate from administration
and supplier-master changes.

## What we built

AegisPay matches invoice evidence against purchase orders, goods receipts and structured PayPal
invoices. A LangGraph workflow combines extraction, forensics and deterministic risk and policy
checks. Human decisions carry a reason and the exact gate hash the reviewer saw. A separate
settlement worker executes frozen commands and confirms PayPal status by GET before recording
invoice payment. Amounts use integer cents; the receiver comes from the approved vendor master.

The Next.js workspace includes AG Studio custom widgets for payables, mandate usage, frozen
exposure, observed decision steps and settlement receipts. The case view separates evidence,
security and money. Operators can inspect quoted findings, approval validity and the locked
payee. The operations panel exposes halt controls, alerts, cooling vendor changes and audit
anchor time. Studio and application views share theme tokens. The Studio Agent Framework
hello-world remains a scripted integration probe; a live Treasury Analyst is not claimed.

## Four demonstration scenarios

1. S1 matches a clean invoice to an accepted value of 48,000 cents.
2. S2 derives a 42,500-cent short-pay, with modelled costs of 6,592 cents for pay-and-claim
   versus 1,500 cents for short-pay plus notice.
3. S3 freezes an injected invoice. Turning the detector off still does not give the document
   control over the vendor-master receiver.
4. S4 blocks a duplicate and links the original execution.

Browser replay exercises authenticated API routes, CSRF, PostgreSQL, graph decisions and SSE.
Replay is visibly labelled and produces no spend-ledger, PO-ledger or settlement-outbox writes.
These scenario results are replay evidence, not proof of fresh sandbox payouts or model accuracy.
Use the actual counts and limitations in [EVAL.md](EVAL.md) for evaluation claims.

## Real and simulated

> In production, the supplier's PayPal account belongs to the supplier. In this demo, the Vendor Simulator holds the supplier app's credentials in order to (a) issue invoices, (b) give AegisPay read access to invoices addressed to the treasury — standing in for consented third-party access through PayPal's partner/permissions model — and (c) reconcile invoices after payment, which in production the supplier's own AR system or PayPal's native invoice payment would do. The decision engine only ever receives a **read** interface to supplier data; supplier write calls live in `vendor_sim/` and are never reachable from an LLM.

PayPal Payouts and Invoicing integrations exist with recorded sandbox spike evidence. Fresh
end-to-end sandbox acceptance and hosted verification remain pending. The supplier portal
currently shows a labelled synthetic preview; authenticated notice accept/contest remains
integration work. Recorded replay responses, seeded purchasing records and the synthetic
Studio preview are explicitly labelled. No claim is made that a simulated receipt proves payment.

## What we learned and limits

Protecting money requires trust boundaries outside prompts: approved receiver identity,
deterministic amount derivation, independent reviewers, frozen commands, durable idempotency
and confirmation of asynchronous results. Browser checks also caught case-response bugs that
graph-only tests missed: an implicit database join and strict integer serialization of sums.

The threat model excludes a compromised host, database owner or colluding authorized operator
pair. Audit anchoring can make some tampering detectable; it does not make those actors harmless.
This is a hackathon build, not a claim of production readiness or demonstrated customer demand.

## Submission assets still needed

- Owner-confirmed hosted frontend and API URLs, followed by clean-browser verification.
- Final public YouTube video under three minutes, with captions and the simulation boundary.
- Fresh sandbox acceptance evidence from Dev A's P1/P2 completion work.
- Final tools disclosure and evaluation snapshot; see [TOOLS.md](TOOLS.md) and [EVAL.md](EVAL.md).

Repository: https://github.com/harshit2408/Aegispay. License: MIT. Hosting configuration targets
Render; tooling and provider disclosures belong in TOOLS.md. Do not paste passwords, tokens
or licence keys into Devpost text.
