import { StudioSpike } from "./studio/studio-spike";

export default function Home() {
  return (
    <>
      <section className="page-header" aria-labelledby="cockpit-title">
        <p className="eyebrow">OPERATIONS / COCKPIT</p>
        <h1 id="cockpit-title">Payables cockpit</h1>
        <p>Review invoice cases, follow settlement and see what needs attention.</p>
        <p className="pending-note">Live payables widgets are not connected yet. The Studio workspace below contains local integration probes.</p>
        <p className="licence-status">
          {process.env.NEXT_PUBLIC_AG_LICENSE_KEY
            ? "Licence supplied at build; entitlement and validity through 2026-12-15 still require confirmation."
            : "No licence supplied. Development evaluation only; validity through 2026-12-15 is unconfirmed."}
        </p>
      </section>
      <StudioSpike />
    </>
  );
}
