---
paths:
  - "backend/**"
  - "docker-compose.yml"
  - "render.yaml"
---
# Backend rule (loaded automatically under backend/)

- Stack: Python 3.12+, FastAPI, SQLAlchemy 2.0 async (`DeclarativeBase`), Postgres everywhere except unit tests (ADR-002), LangGraph with `AsyncPostgresSaver`, httpx, pydantic v2 + pydantic-settings.
- Layout is fixed by PRD §6. New top-level modules need a reason in the commit message.
- Schemas: exactly PRD §8 (SQL) and §11.1 (Pydantic). Status enums are closed sets — add a value only by updating the PRD.
- Tests live in `backend/tests/{unit,property,contract,eval,e2e}`; PayPal calls are mocked with `respx` from recorded fixtures in `tests/fixtures/`.
- `[SPIKE-n]` behaviour not yet recorded in PRD §18 → stub + test only.
- Logs are structured JSON with `execution_id`, node name and `paypal-debug-id`. Never log secrets, tokens or full webhook bodies at INFO.
- Config only via `app/config.py` (pydantic-settings) reading env. `.env` is git-ignored; update `.env.example` when adding a var.
- PayPal client code (OAuth token cache, error shape, retries, test fixtures): consult the `paypal:python-*` skills (APIMatic). SDK has no Payouts/Invoicing, so those clients stay httpx; treat the skills as patterns, not generators. Log real use in `docs/TOOLS.md`.
- Lint: `ruff check` and `ruff format`. No `print`.
- The decision engine never imports from `settlement/`; it hands off by writing a `settlement_outbox` row (ADR-001).
