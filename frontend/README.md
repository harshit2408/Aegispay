# AegisPay frontend (P1-13 · P1-14 · P1-15)

Based on the [official PayPal + AG Grid hackathon boilerplate](https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate/tree/5de114eb4306e844396022f9ca0558a4937761e2), pinned to `5de114eb4306e844396022f9ca0558a4937761e2`.

Run with Node 22.18+ (CI uses Node 22):

```powershell
cd frontend
npm ci
npm test
npm run lint
npm run build
npm run dev -- --port 3000
```

Open `http://localhost:3000`. Click **Test React state** inside the custom Studio widget. Choose **Edit Studio layout**, then select **Say hello (scripted)** in Studio's AI side panel. The native Studio harness runs a custom scripted runner and displays a hello-world response. It makes no LLM/PayPal requests, has no tools, needs no credentials, and does not implement the P3-11 Treasury Analyst. The persistent REPLAY banner labels the synthetic probe data.

The shared navigation opens the cockpit (`/`), the synthetic case workspace (`/cases/preview`), mandate studio (`/mandate`) and clearly labelled supplier simulator (`/vendor`). The native collapsible judge guide explains S1–S4, operator access and the simulation boundary. The REPLAY banner remains visible on every route. Unknown case IDs keep the unloaded-case state.

The mocked case shows Evidence, Security and Money panes: an explicitly synthetic invoice rendering with hidden-text bounding boxes, source comparison, locked vendor-master payee and change provenance, raw injection text, risk contributions, quoted investigator facts, supplied short-pay costs and assumptions, approval progress, voided approvals, and an unreached settlement timeline. HIGH_RISK hides the investigator recommendation. There is no externally verified audit anchor, so audit export is disabled with an explanation.

Use **Synthetic reviewer** and a reason of at least 10 characters to rehearse a gate-allowed decision locally. The uploader and receiver cannot approve; duplicate reviewers do not count twice. **Simulate case change** invalidates earlier local approvals and requires reviewing the refreshed gate. All local choices disappear on reload. This is not sign-in or an actual approval: it makes no API, PayPal or LLM request and never starts settlement. Mandate/supplier mutations and authenticated case integration remain later work.

API types in `src/lib/api.ts` are generated from the actual FastAPI app's committed `backend/openapi.json`, using the PRD §11.1 Pydantic models and §13 case envelope. Regenerate from the repository root in an installed backend environment, without loading a real environment file:

```powershell
python backend/scripts/export_openapi.py
cd frontend
npm run api:generate
npm run api:check
```

`python backend/scripts/export_openapi.py --check` detects server/schema drift; `npm run api:check` detects generated TypeScript drift. The backend contract test also compares the committed OpenAPI to the actual app. The three published case routes explicitly return **501** until operator authentication, CSRF enforcement and the case service are implemented; they perform no business operations. The typed client in `src/lib/client.ts` includes session cookies, requires a session CSRF token for decisions, sends only action/reason/gate hash, preserves API errors and reports stale gates as “Case changed, review again.” The preview does not call it. Only implemented or explicitly stubbed server paths appear in OpenAPI; the remainder of PRD §13 is not advertised as available.

Q4 uses the official AG Studio `studioTheme.withParams` dark base with the existing shared CSS tokens in `globals.css`; case panes reuse the same surfaces, typography, focus and contrast colors. There is no second terminal aesthetic or new UI library. The app retains upstream's ESLint/PostCSS setup and Studio provider/widget wiring. The Next config adds standalone output, the TypeScript alias moves to `src/`, and the root layout avoids a build-time Google Fonts request. `openapi-typescript` is a development-only generator, not a browser runtime dependency. The direct PayPal reader, billing pages, report-management shell and unauthenticated paid-model proxy are excluded: the AegisPay backend owns PayPal access and later authenticated AI calls.

AG Studio is commercial software. Without a key this is development evaluation with vendor warnings/watermarks. Have the account owner supply a Studio licence **including AI** valid through **2026-12-15**, set `NEXT_PUBLIC_AG_LICENSE_KEY` in Render's build environment and rebuild. Never commit or send the key in chat. A supplied value alone does not prove entitlement or expiry; record confirmation in `docs/spikes/SPIKE-7.md` and repeat the browser check. No trial or paid plan was activated by this spike.

The Dockerfile builds Next standalone and runs it as a non-root user on port 3000. The optional `NEXT_PUBLIC_API_URL` build argument selects the typed client's API origin; the synthetic case does not call it. Complete S1–S4 pages, SSE and authenticated money actions belong to later checklist items.

API references for the retained feasibility component: [custom widgets](https://www.ag-grid.com/studio/react/custom-widgets/), [custom runner](https://www.ag-grid.com/studio/react/ai-custom-runner/), [Agent Framework](https://www.ag-grid.com/studio/react/ai-agents/).
