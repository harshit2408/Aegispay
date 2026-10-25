---
tags: [aegispay, tools]
source: "[[PRD_SRS]] §1 note 5, §21"
---
# Tools disclosure (submission requirement)

## APIMatic Context Plugin for PayPal (P0-05)
- [ ] Participant subscription claimed (date: ____)
- Install (from the APIMatic/PayPal getting-started guide): `npx context-plugins install https://github.com/paypaldev/server-sdk-context-plugin-preview` (repo `paypaldev/server-sdk-context-plugin-preview`), then prompt the agent to build with PayPal.
- [x] Plugin installed (Dev A, 2026-10-22, user scope): `~/.context-plugins/installed.json` lists `paypal` v0.1.0, targets claude/cursor/vscode; enabled as `paypal@context-plugins-local` in `~/.claude/settings.json`.
- Coverage (checked 2026-10-22): bundled skills teach the **PayPal Server SDK** (`paypal-server-sdk` 2.4.0; Python skills: auth, client init, calling endpoints, models, error handling, resilience, testing). That SDK has controllers for OAuth, Orders, Payments, Subscriptions, Transaction Search, Vault. It has **no Payouts or Invoicing controller**, so the Treasury and Vendor clients (PRD §9) are hand-written `httpx` and are not plugin-generated.
- Honest use: log only code the plugin really informed (e.g. OAuth token handling, error handling, Transaction Search for the `list_transactions` read). Do not claim Payouts/Invoicing code.
- [x] Skills load in Claude (checked 2026-10-22): 48 `paypal:<lang>-<topic>` skills (csharp, java, php, python, ruby, typescript × authentication, calling-endpoints, client-initialization, configuration-resilience, error-handling, getting-started, models, testing). Routed: Python only, via `.claude/routing.json` route `paypal-sdk`. Other languages unused (backend is Python; browser never calls PayPal).
- Usage log (append one line each time it generates PayPal client code): date · dev · what was generated

## AI models + IDs
**P0-16 / Q3 decided 2026-10-26, Dev B under the user's P0 closeout delegation:** use one provider, OpenAI, and one model for the initial implementation. The existing **$1.00 USD per invoice execution** cap remains; this is a selection decision, not a report of working inference or measured accuracy.

| Configuration | Exact value | Planned use (§10) |
|---|---|---|
| `LLM_PROVIDER` | `openai` | API process only (R22) |
| `LLM_MODEL_EXTRACT` | `gpt-6.1-sol` | A2: rendered invoice pages + text, schema-validated extraction |
| `LLM_MODEL_REASON` | `gpt-6.1-sol` | A1/A3/A4/A5 and optional A6, within their existing trust/tool boundaries |

The [official model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) confirms image input, structured outputs and the exact selector above. Use Responses for A4 tool calls. A2/A3/A5/A6 still have zero tools; A4 sees only application-owned read tools, with no provider-hosted tools enabled. All model output remains untrusted evidence; deterministic code owns amount, payee and gate decisions (R2/R3/R14).

Selection rationale: image input and structured output cover extraction and classification without a second integration. The [current model catalogue](https://developers.openai.com/api/docs/models) positions Sol between Astra's capability and Luna's cost. Sol is the initial quality-oriented choice within the agreed cap; cheaper Luna routing and additional providers add evaluation work and are deferred until §10.1 supplies evidence. Published capabilities do not establish accuracy on our invoices or injection cases.

Pricing checked **2026-10-26**: Standard, prompts ≤272K input tokens, **$2 input / $10 output per million tokens**. [Official pricing](https://developers.openai.com/api/docs/pricing) also lists caching, long-context and processing-tier rates; do not apply a cached/Batch/Flex discount to an ordinary request estimate. For illustration, four requests each using 40,000 billable input tokens and 8,000 total billable output tokens cost `4 × (40,000 × 2 + 8,000 × 10) / 1,000,000 = $0.64`. This is a planning example, not a token allowance or observed invoice cost; images and reasoning must be included in billable usage.

Implementation acceptance for §16: at most four model requests per invoice execution, including retries and A4 continuation requests; all share the $1 cap. Reserve a conservative maximum cost before each request, including the output limit, image tokens, cache writes and any applicable premiums; fail closed if it cannot fit the remaining budget. Reconcile provider usage and log tokens/cost per execution without document text. A1 mandate authoring and optional A6 simulator runs need their own bounded executions and never debit an invoice silently. Runtime enforcement, paid API access and the §10.1 eval remain unverified until their application tasks land. Keep `LLM_API_KEY` in ignored local/service configuration only.

## PayPal APIs / Agent Toolkit / AG Studio / Render
Pending (P5-02). Notes so far:
- Agent Toolkit: `paypal-agent-toolkit` 1.1.0 (1.11.0 not installable, pins `crewai-tools==0.13.2`). Read-only config exposes `get_invoice`, `list_invoices` only (SPIKE-6). It has no `list_transactions`, so the Treasury read tool is our own with the same name.
