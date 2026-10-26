---
paths:
  - "backend/app/agents/**"
  - "backend/app/decision/**"
  - "backend/app/forensics/**"
  - "backend/app/paypal/toolkit.py"
  - "backend/app/vendor_sim/notices.py"
  - "backend/app/vendor_sim/negotiator.py"
  - "backend/app/routers/mandates.py"
  - "backend/tests/eval/**"
---
# AI-agents rule (loaded automatically for LLM / agent code)

- Components A1–A7 are specified in PRD §10. Do not add an LLM call that is not in that table; deterministic logic (risk, gate, short-pay) stays pure Python.
- **R2** LLM-reachable tools are read-only. `paypal/toolkit.py` asserts at startup that no tool name matches `create|send|cancel|update|refund|capture|payout`.
- **R14** A2/A3/A5/A6: zero tools, document and vendor text inside clear delimiters as data, schema-validated output, low confidence or failure ⇒ `FREEZE` (fail closed). A4 sees vendor fields wrapped as untrusted; its facts carry verbatim excerpts. A5 gets whitelisted fields only — never gate reasons, thresholds or mandate values.
- LLM outputs are evidence only (R3). They never set amount, payee or route. The investigator's recommendation is advisory.
- Drafted notices: numbers inserted by template after generation.
- Graph: dynamic `interrupt()` in `human_review`; resume carries only an `approval_id`; `human_review → match → … → policy_gate` re-reads DB facts (R11); Postgres checkpointer.
- A1 mandate fields are all clamped to `ORG_HARD_*`; activation needs a second operator (R13).
- Eval: ≥ 50 PDFs, held-out split, 5 runs, per-class counts with Wilson intervals (PRD §10.1).
- Every LLM component has eval cases in `backend/tests/eval/` and results in `docs/EVAL.md` (actuals, not targets).
- Record provider + exact model IDs in `docs/TOOLS.md`. Invoice calls use one shared `app.agents` TokenOps run: $1 total, ≤ 4 provider requests including retries/continuations, no direct provider calls. Inject governed completions into each component; log usage without prompts. See `docs/TOKENOPS.md` for the API and current process-local limits.
