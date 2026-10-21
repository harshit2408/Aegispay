---
paths:
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
- **R14** Extractor and classifier: zero tools, document text inside clear delimiters as data, schema-validated output, low confidence ⇒ `FREEZE`.
- LLM outputs are evidence only (R3). They never set amount, payee or route. The investigator's recommendation is advisory.
- Drafted notices: numbers inserted by template after generation.
- Graph: dynamic `interrupt()` in `human_review`; `human_review → policy_gate` always (ADR-005); Postgres checkpointer.
- Every LLM component has eval cases in `backend/tests/eval/` and results in `docs/EVAL.md` (actuals, not targets).
- Record provider + exact model IDs in `docs/TOOLS.md`. ≤ 4 LLM calls per invoice; log tokens per execution.
