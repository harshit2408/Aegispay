# AegisPay Studio feasibility spike (P0-13)

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

The app uses the upstream package versions/lock and copies its ESLint/PostCSS setup and Studio theme (`src/app/studio/theme.ts`) unchanged. The Next config adds standalone output, the TypeScript alias moves to `src/`, the root layout avoids a build-time Google Fonts request, and the dashboard retains upstream's `AgStudioProvider`/`AgStudio`/theme wiring with a minimal spike report. The direct PayPal reader, billing pages, report-management shell and unauthenticated paid-model proxy are excluded: the AegisPay backend owns PayPal access and later authenticated AI calls.

AG Studio is commercial software. Without a key this is development evaluation with vendor warnings/watermarks. Have the account owner supply a Studio licence **including AI** valid through **2026-12-15**, set `NEXT_PUBLIC_AG_LICENSE_KEY` in Render's build environment and rebuild. Never commit or send the key in chat. A supplied value alone does not prove entitlement or expiry; record confirmation in `docs/spikes/SPIKE-7.md` and repeat the browser check. No trial or paid plan was activated by this spike.

The Dockerfile builds Next standalone and runs it as a non-root user on port 3000. The optional `NEXT_PUBLIC_API_URL` build argument is reserved for the generated backend client (P1-14); this spike does not call it. Real backend API types, S1–S4 pages, SSE and money actions belong to their later checklist items.

API references: [custom widgets](https://www.ag-grid.com/studio/react/custom-widgets/), [custom runner](https://www.ag-grid.com/studio/react/ai-custom-runner/), [Agent Framework](https://www.ag-grid.com/studio/react/ai-agents/).
