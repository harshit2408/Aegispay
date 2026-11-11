"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { components } from "@/lib/api";
import { api } from "@/lib/client";
import { demoMode } from "@/lib/demo-display";
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
    {!error && mode?.enabled && operator && !busy && ["admin", "controller"].includes(operator.role) &&
      <DemoControls key={`${operator.operator_id}:${operator.csrf_token}`} operator={operator} replayed={mode.replayed} />}
  </>;
}

function DemoControls({ operator, replayed }: { operator: components["schemas"]["SessionView"]; replayed: boolean }) {
  const [control, setControl] = useState<components["schemas"]["DemoControlView"]>();
  const [receipt, setReceipt] = useState<components["schemas"]["DocumentReceipt"]>();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  async function run(action: () => Promise<void>) {
    if (pending) return;
    setPending(true); setMessage(""); setReceipt(undefined);
    try { await action(); } catch (error) { setMessage(error instanceof Error ? error.message : "Demo action could not be confirmed."); }
    finally { setPending(false); }
  }
  return <section className="demo-controls" aria-label={operator.role === "admin" ? "Demo toolbar" : "Replay scenario runner"} aria-busy={pending}>
    <h2>{operator.role === "admin" ? "Demo toolbar" : "Replay scenario runner"}</h2>
    {!replayed && <p>Live reset and scenario orchestration are not yet connected. Use the owner sandbox runbook.</p>}
    {operator.role === "admin" ? <>
      <p>Reset starts a new run. Forensics state applies to newly run scenarios only.</p>
      <div className="studio-toolbar">
        <button type="button" disabled={pending || !replayed} onClick={() => void run(async () => { setControl(await api.resetDemo(operator.csrf_token)); setMessage("New replay run ready. Existing payment history is preserved."); })}>Reset run</button>
        <button type="button" disabled={pending || !replayed} onClick={() => void run(async () => { setControl(await api.setDetector(true, operator.csrf_token)); setMessage("Forensics detector ON for new scenarios."); })}>Forensics detector ON</button>
        <button type="button" disabled={pending || !replayed} onClick={() => void run(async () => { setControl(await api.setDetector(false, operator.csrf_token)); setMessage("Forensics detector OFF for new scenarios. Payee remains locked."); })}>Forensics detector OFF</button>
      </div>
      <p>Sign in as a controller to run S1–S4. Scenario upload identity stays separate from administration.</p>
      {control && <p>Run {control.run_id} · Detector {control.detector_enabled ? "ON" : "OFF"}</p>}
    </> : <div className="studio-toolbar">{(["S1", "S2", "S3", "S4"] as const).map((scenario) =>
      <button key={scenario} type="button" disabled={pending || !replayed} onClick={() => void run(async () => { setReceipt(await api.runScenario(scenario, operator.csrf_token)); setMessage(`${scenario} admitted. Open the case for server results.`); })}>Run {scenario}</button>)}
    </div>}
    <p role="status">{message}</p>
    {receipt && <p><Link href={`/cases/${encodeURIComponent(receipt.execution_id)}`}>Open scenario case</Link>
      {receipt.duplicate_of_execution_id && <> · <Link href={`/cases/${encodeURIComponent(receipt.duplicate_of_execution_id)}`}>Open original execution</Link></>}</p>}
  </section>;
}
