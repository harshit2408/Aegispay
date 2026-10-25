---
paths:
  - "frontend/**"
---
# Frontend rule (loaded automatically under frontend/)

- Stack: Next.js App Router + TypeScript, started from the official PayPal + AG Grid hackathon boilerplate; AG Studio cockpit with custom widgets (PRD §14.2) is the sponsor target.
- Pages: `/` cockpit, `/cases/[id]`, `/mandate`, `/vendor` (labelled "Simulator"), plus the judge-guide panel (PRD §14.1).
- API types come only from `src/lib/api.ts`, generated from the backend OpenAPI. Live updates via `src/lib/sse.ts` (`EventSource`).
- The UI never computes payable amounts or accepts a payee. Actions render only from `GateDecision.allowed_actions`; each needs a reason.
- Show "Payee source: vendor master (locked)" on the case view; show a persistent REPLAY banner when replay mode is on.
- One custom dark theme shared by Studio and the app. No new UI library without a reason in the commit.
- Accessibility: labelled controls, keyboard reachable, visible focus, AA contrast. Never cut it.
- Secrets: `NEXT_PUBLIC_AG_LICENSE_KEY` comes from Render env only — never commit it.
- E2E: Playwright for S1–S4 in replay mode (`ecc:e2e-runner`).
