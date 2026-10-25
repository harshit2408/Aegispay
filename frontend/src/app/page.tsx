import { StudioSpike } from "./studio/studio-spike";

export default function Home() {
  return (
    <main id="main">
      <div className="replay-banner">REPLAY · SPIKE-7 · Synthetic probe data · No PayPal or LLM calls</div>
      <header>
        <p className="eyebrow">AEGISPAY / STUDIO INTEGRATION</p>
        <h1>AG Studio feasibility cockpit</h1>
        <p>Official PayPal + AG Grid starter · Custom React widget · Scripted Agent Framework hello-world</p>
        <p className="licence-status">
          {process.env.NEXT_PUBLIC_AG_LICENSE_KEY
            ? "Licence supplied at build; entitlement and validity through 2026-12-15 still require confirmation."
            : "No licence supplied. Development evaluation only; validity through 2026-12-15 is unconfirmed."}
        </p>
      </header>
      <StudioSpike />
    </main>
  );
}
