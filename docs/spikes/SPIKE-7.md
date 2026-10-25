# SPIKE-7 — AG Studio integration (P0-13)

2026-10-25 · Dev B · PRD §14.2 / §18

**Result: technical feasibility PASS in development evaluation; licence acceptance remains open.** The official starter builds and runs with a React custom widget and a scripted hello-world agent in the native Studio Agent Framework. No licence key was inspected or supplied, and validity through 2026-12-15 has not been established. P0-13 must stay unchecked until that external condition is confirmed.

## Source and version evidence

- [Official PayPal + AG Grid boilerplate](https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate/tree/5de114eb4306e844396022f9ca0558a4937761e2), commit `5de114eb4306e844396022f9ca0558a4937761e2`.
- Dependency graph copied from its lock: Next.js `16.3.6`, React/React DOM `19.2.8`, AG Studio/React `3.0.0`, AG Grid Community/React `36.2.0`. The lock differs only in the app name.
- `eslint.config.mjs`, `postcss.config.mjs` and `src/app/studio/theme.ts` are copied unchanged. The theme reads upstream's `--pp-*` token names; the app sets dark values. TypeScript keeps upstream strict settings, adds the native test import option and points `@/` at `src/`.
- `studio-spike.tsx` adapts upstream `app/dashboard/dashboard.tsx` provider/module/theme wiring and `studio/ai.ts` harness callback. The minimal report replaces direct PayPal reads and the paid-model proxy. These are owned by the AegisPay backend, and the upstream proxy warns it lacks authentication/rate limiting.

Official current API references read before implementation:

- [Quick start](https://www.ag-grid.com/studio/react/quick-start/).
- [Custom widgets](https://www.ag-grid.com/studio/react/custom-widgets/): React component in `AgWidgetDefinition.comp`; import `createWidgets` from `ag-studio-react`; register custom types in an `AgBaseRegistry` extension.
- [Agent overview](https://www.ag-grid.com/studio/react/ai-agents/) and [custom runner](https://www.ag-grid.com/studio/react/ai-custom-runner/): supply `{ id, run }` in the `createAiHarness` config builder; the runner emits its own run boundaries and message events.
- [Hackathon sponsor resources](https://paypalaihackathon.devpost.com/details/aggrid): the published trial lasts 45 days. Even if started on Oct 25, that duration alone cannot establish coverage through Dec 15; the account owner needs explicit expiry confirmation or an extension.

## Reproduce

From `frontend/`, use Node 22.18+ and run `npm ci`, `npm test`, `npm run lint`, `npm run build`, then `npm run dev -- --port 3000`. No credentials are required for this local probe. Open `/`, click **Test React state**, choose **Edit Studio layout**, and use **Say hello (scripted)** in Studio's AI panel. Return to view mode with **View Studio layout**.

The persistent REPLAY banner identifies synthetic data. There are no money fields, amount calculations, payment actions, backend requests, LLM adapters or secrets in the spike. The custom agent advertises no tools; it answers with a fixed labelled greeting. This verifies the framework runner, harness, message events and chat UI without introducing an unapproved application LLM component. The P3-11 Treasury Analyst remains separate work.

## Actual validation

Build/lint/unit and root checks repeated on 2026-10-26 after resuming; browser observations below are from the 2026-10-25 worker. Local Node 24 / Windows, isolated worktree, no real `.env` files read:

| Check | Observed result |
|---|---|
| `npm ci --no-audit --no-fund` | PASS, 369 packages; first network-reset attempt retried successfully |
| `npm test` | PASS, 2 tests: complete run/message sequence, no tools, pre-aborted run emits nothing |
| `npm run lint` | PASS |
| `npm run build` | PASS, optimized static `/` and `/_not-found`, strict TypeScript passes |
| Production standalone server | PASS on `127.0.0.1:3813`; static/public copied using the Dockerfile's layout |
| Browser, isolated Chrome context | PASS: Studio ready, custom widget renders, click count 0 → 1, edit/view toggle, native AI chat returns the scripted greeting |
| Browser network resource origins | Local origin only; no PayPal or LLM request |
| Licence diagnostics | Expected vendor `AG Studio Pro with AI License / License Key Not Found`; evaluation unlocks features with watermark. No warning suppression or licence workaround |
| Root Ruff / backend pytest | PASS / 6 passed |
| Root Node harness | Resume validation PASS, 11/11; earlier interrupted session had an isolated 10s subprocess timeout |
| Complete Claude/Codex mirror check | PASS after regenerating fresh-checkout CRLF ports locally; no harness source or semantic mirror changes |
| Container runtime | Not tested locally: parent reports Docker Linux engine unavailable. P0-18 coordinates Linux CI smoke |

React/TypeScript review disciplines applied locally because the named ECC reviewer agents are not exposed as selectable tools here. Checked registry typing, no runtime `any`/casts, stable widget/harness definitions, labelled controls, focus style, no credentials or provider proxy, no payment calls and no client money logic.

Known UX limits: Studio's default view mode exposes Filters only; AI appears in edit mode. Studio 3.0 emits an `aria-hidden` focus warning after clicking its suggested prompt (the hidden prompt retains focus). This belongs to vendor chat internals; keyboard operation and a future upgrade need a full a11y pass before P3-11. This is a desktop integration probe, not S1–S4 or a mobile layout. The browser also requested a missing default favicon (404); app/Studio code had no runtime exception.

## Remaining account-owner action

1. Obtain the legitimate Studio trial/entitlement **including AI**, with explicit written validity through **2026-12-15**. Do not paste the key into chat or commit it.
2. Set `NEXT_PUBLIC_AG_LICENSE_KEY` in the frontend Render **build environment**, then rebuild. A Next public licence is included in the browser bundle as required by the vendor; PayPal/LLM credentials must never use public variables.
3. Repeat the browser steps, confirm no invalid/expired/missing-AI diagnostics, and record the non-secret entitlement/expiry confirmation here. Setting an arbitrary nonempty environment value is not proof of validity.

No licence request, message, account purchase or paid-plan activation was sent. Provider selection/credentials are unnecessary for the scripted hello-world and do not block its result.
