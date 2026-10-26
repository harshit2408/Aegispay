# TokenOps integration (P0-25)

The backend supplies one shared completion boundary in `app.agents` for the planned
PRD §10 invoice agents. It follows the upstream
[integration skill](https://github.com/theagentplane/tokenops/blob/main/.claude/skills/integrate-tokenops/SKILL.md),
Tier 1: `tokenops_run` owns the run and `wrap_complete` governs each model request.
There are no backend inference agents or graph routes yet. The frontend Studio probe
is scripted and makes no model requests.

## Use from the workflow owner

Install the backend normally: `.venv/Scripts/python.exe -m pip install -e 'backend[dev]'`.
No additional environment variables or control-plane server are required.

```python
from app.agents import invoice_run
from tokenops.framework import Halt

# execution_id is a trusted UUID from the database, not a client-supplied trace ID.
# single_request_dispatch is the application-owned provider adapter described below.
try:
    with invoice_run(execution_id, dispatch=single_request_dispatch) as run:
        extraction = run.complete("A2", extraction_messages, input_token_bound=extract_bound)
        classification = run.complete("A3", classifier_messages, input_token_bound=classify_bound)
        # Optional A4/A5 completions use this SAME run object and remaining budget.
except Halt:
    # Route to the existing component's fail-closed outcome; no settlement command.
    ...
```

Inject `run.complete` into components instead of constructing a provider client in
each node. `Halt` inherits from `BaseException`; catch it explicitly at the workflow
boundary. A2/A3 failures retain their PRD §10 fail-closed outcomes. Do not translate
a halt into approval, retry through an ungoverned client, or silently start another run.

The boundary is synchronous and sequential within its owning thread. Run the whole
scope in one worker thread when called by an async route. Do not share the handle
between threads, fan out completions, or keep it after the context exits. A1 mandate
authoring and A6 simulator work require separate bounded workflows when implemented;
they are not charged as invoice nodes. The library does not grant any tools or change
the R2/R3/R14 trust boundaries.

## Dispatch contract

`dispatch(provider, model, messages, max_output_tokens=...)` must issue exactly one
non-streaming OpenAI `gpt-6.1-sol` request with SDK retries **disabled**. It must enforce
the supplied output limit (currently 1,024 tokens, including reasoning), and return
`tokenops.providers.types.ModelResponse` with real, inclusive billable `input_tokens`
and `output_tokens`. Cached/reasoning detail counts are subsets of those totals.
Provider errors or invalid/over-bound usage permanently halt the current run because
the final bill may be unknown. Request exceptions are not logged with provider text.

The application-owned adapter must use ordinary Standard pricing without cache-write
charges, regional premiums, provider-hosted tool fees or other surcharges. No provider
adapter is supplied here; implementing one remains part of the component work. The
selected model's Standard short-context rates are $2 input / $10 output per million
tokens ([official pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
checked 2026-10-26). Cached input is conservatively charged at full input price;
reasoning is already included in output. Unknown providers/models have no price and
are refused. Any pricing-mode extension needs reviewed estimates and usage tests first.

`input_token_bound` is a **trusted upper bound on all billable input tokens** for the
actual request: text, images, framing, tool schemas and continuation context. It must
be an integer in 1..272,000; exceeding that range enters a different pricing band and
is refused. Never derive it from TokenOps' text-length estimate or from vendor/request
data. The future provider adapter must calculate a conservative bound and enforce the
request mode; missing bounds must fail closed. Underestimation is detected after the
response and halts, but cannot undo provider charges already incurred.

## Enforcement and privacy

All A2/A3/A4/A5 requests in a scope share one trace and ledger. Both `cost_budget`
and `pre_call_worst_case` are enabled for a 1,000,000 micro-USD ($1) budget. The latter
refuses a request whose input bound plus maximum output would reach or exceed the
remaining budget. A separate pre-dispatch count preserves the fourth result and
blocks request five, including A4 continuations. Automatic wrapper retries are disabled.
Upstream's step policy trips after a response, which would discard result four.

Usage logs are JSON containing the execution UUID, node and numeric usage/cost only.
The Chronicle recording scope disables input/output capture and recording and supplies
no sink or export client. An ambient Chronicle export URL does not enable invoice
body export. Nothing configures global tracing, reads credentials, or provisions an
AgentPlane control plane. Importing `app.agents` sets `PYTHON_DOTENV_DISABLED=1`
before loading TokenOps, preventing the SDK's implicit dotenv loader from searching
for credential files. This process setting is intentional; `app.config` remains the
settings owner through pydantic-settings. Import the application boundary before any
direct TokenOps import; standalone SDK smoke commands need the same environment flag.

## Current limit and adoption gate

Tier 1 accounting lives in memory. A new context starts a new budget; an execution UUID
is a log correlation value, not durable deduplication. This foundation **does not yet
satisfy PRD §16 across restarts/resumes or multiple workers**. Before enabling model
requests on those paths, preserve the spent amount, request count and uncertain/halted
state with a shared durable ledger and conservative admission. Never reopen an invoice
with a fresh allowance after human review or recovery. Tier 2 is the upstream next step
when processes must share a budget; that deployment is not part of this integration.

## Offline verification

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/unit/test_tokenops.py
$env:PYTHON_DOTENV_DISABLED = '1'
.venv/Scripts/python.exe -m tokenops.demo
```

The application tests use the real TokenOps governor/Chronicle crossing hooks with
fake provider responses. They cover shared usage, pre-call budget refusal, request
limits, sticky failure, malformed usage, input bounds and disabled recording/export.
The upstream demo uses its own $2 budget and local fake model; it is a package smoke
test, not evidence of the application's $1 cap. No live provider access or model
accuracy is claimed. Existing component evaluations remain on their original tasks.
