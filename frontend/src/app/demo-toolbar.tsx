"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { components } from "@/lib/api";
import { api } from "@/lib/client";
import { demoFailure, demoMode, showsDemoToolbar } from "@/lib/demo-display";
import { useSession } from "./session";

export function DemoWorkspace() {
  const [mode, setMode] = useState<ReturnType<typeof demoMode>>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const { operator, busy } = useSession();
  useEffect(() => {
    const controller = new AbortController();
    void api.health(controller.signal).then((data) => { if (!controller.signal.aborted) { setMode(demoMode(data)); setError(""); } })
      .catch(() => { if (!controller.signal.aborted) setError("Demo status unavailable. Controls are disabled."); });
    return () => controller.abort();
  }, [attempt]);
  return <>
    {mode?.enabled && <div className="sandbox-banner">SANDBOX DEMO · Supplier and purchasing records include simulation</div>}
    {mode?.replayed && <div className="replay-banner">REPLAY · Recorded responses · No live money movement</div>}
    {error && <p role="status">{error} <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry demo status</button></p>}
    {!error && mode && operator && !busy && showsDemoToolbar(mode, operator.role) &&
      <DemoControls key={`${operator.operator_id}:${operator.csrf_token}`} csrfToken={operator.csrf_token} replayed={mode.replayed} />}
  </>;
}

const LIVE_ONLY_REASON = "Replay only: live detector and scenario admission are not available on this server. Upload invoices from the Inbox as a controller.";

function DemoControls({ csrfToken, replayed }: { csrfToken: string; replayed: boolean }) {
  const [control, setControl] = useState<components["schemas"]["DemoControlView"]>();
  const [receipt, setReceipt] = useState<components["schemas"]["DocumentReceipt"]>();
  const [pending, setPending] = useState("");
  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState("");
  async function run(label: string, action: () => Promise<string>) {
    if (pending) return;
    setPending(label); setMessage(""); setFailure(""); setReceipt(undefined);
    try { setMessage(await action()); } catch (error) { setFailure(demoFailure(error)); }
    finally { setPending(""); }
  }
  const detector = control?.detector_enabled;
  return <section className="demo-controls" aria-labelledby="demo-toolbar-title" aria-busy={Boolean(pending)}>
    <h2 id="demo-toolbar-title">Demo toolbar</h2>
    <p>Mode: <strong>{replayed ? "REPLAY (recorded sandbox responses)" : "LIVE sandbox"}</strong>. Reset starts a new run; payment history is preserved. Forensics state applies to newly run scenarios only.</p>
    <div className="studio-toolbar">
      <button type="button" disabled={Boolean(pending)} onClick={() => void run(replayed ? "Resetting replay run…" : "Resetting: waiting for the owner reset runner (up to 25 s)…", async () => {
        const view = await api.resetDemo(csrfToken);
        setControl(view);
        return `Run ${view.run_id} ready${view.previous_run_id ? `, replacing ${view.previous_run_id}` : ""}. Detector ON.`;
      })}>Reset run</button>
      {([true, false] as const).map((enabled) =>
        <button key={String(enabled)} type="button" aria-pressed={detector === enabled} aria-describedby={replayed ? undefined : "demo-live-reason"}
          disabled={Boolean(pending) || !replayed} onClick={() => void run(`Turning forensics detector ${enabled ? "ON" : "OFF"}…`, async () => {
            setControl(await api.setDetector(enabled, csrfToken));
            return enabled ? "Forensics detector ON for new scenarios." : "Forensics detector OFF for new scenarios. Payee remains locked.";
          })}>Forensics detector {enabled ? "ON" : "OFF"}</button>)}
    </div>
    <div className="studio-toolbar">
      {(["S1", "S2", "S3", "S4"] as const).map((scenario) =>
        <button key={scenario} type="button" aria-describedby={replayed ? undefined : "demo-live-reason"} disabled={Boolean(pending) || !replayed}
          onClick={() => void run(`Running ${scenario}…`, async () => {
            setReceipt(await api.runScenario(scenario, csrfToken));
            return `${scenario} admitted. Open the case for server results.`;
          })}>Run {scenario}</button>)}
    </div>
    {!replayed && <p id="demo-live-reason">{LIVE_ONLY_REASON}</p>}
    {control && <p>Run {control.run_id} · Detector {control.detector_enabled ? "ON" : "OFF"} (server confirmed)</p>}
    <p role="status">{pending || message}</p>
    <p role="alert">{failure}</p>
    {receipt && <p><Link href={`/cases/${encodeURIComponent(receipt.execution_id)}`}>Open scenario case</Link>
      {receipt.duplicate_of_execution_id && <> · <Link href={`/cases/${encodeURIComponent(receipt.duplicate_of_execution_id)}`}>Open original execution</Link></>}</p>}
  </section>;
}
