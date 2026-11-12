"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { components } from "@/lib/api";
import { api } from "@/lib/client";
import { controlSummary, createDemoRunner, demoFailure, demoMode, detectorPressed, IDLE, showsDemoToolbar, type DemoRunState } from "@/lib/demo-display";
import { useSession } from "./session";

export function DemoWorkspace() {
  const [mode, setMode] = useState<ReturnType<typeof demoMode>>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  // Held above the keyed toolbar so an in-flight action survives a session-driven remount.
  const runner = useRef(createDemoRunner());
  const [runState, setRunState] = useState<DemoRunState>(IDLE);
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
      <DemoControls key={`${operator.operator_id}:${operator.csrf_token}`} csrfToken={operator.csrf_token} replayed={mode.replayed}
        run={(label, action) => runner.current.run(label, action, setRunState)} runState={runState} />}
  </>;
}

type Control = components["schemas"]["DemoControlView"];

function DemoControls({ csrfToken, replayed, run, runState }: {
  csrfToken: string; replayed: boolean; runState: DemoRunState;
  run: (label: string, action: () => Promise<string>) => Promise<void>;
}) {
  const [control, setControl] = useState<Control>();
  const [stale, setStale] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [receipt, setReceipt] = useState<components["schemas"]["DocumentReceipt"]>();
  useEffect(() => {
    const controller = new AbortController();
    void api.detector(controller.signal).then((view) => { if (!controller.signal.aborted) { setControl(view); setStale(false); } })
      .catch((error: unknown) => { if (!controller.signal.aborted) setLoadError(demoFailure(error)); });
    return () => controller.abort();
  }, []);
  const pending = Boolean(runState.pending);
  // Reset and detector changes make the shown state stale until the server confirms the new one.
  function change(label: string, action: () => Promise<Control>, message: (view: Control) => string) {
    void run(label, async () => {
      setStale(true); setReceipt(undefined);
      const view = await action();
      setControl(view); setStale(false); setLoadError("");
      return message(view);
    });
  }
  return <section className="demo-controls" aria-labelledby="demo-toolbar-title">
    <h2 id="demo-toolbar-title">Demo toolbar</h2>
    <p>Mode: <strong>{replayed ? "REPLAY (recorded sandbox responses)" : "LIVE sandbox"}</strong>. Reset starts a new run; payment history is preserved. Forensics state applies to newly run scenarios only.</p>
    <div className="studio-toolbar">
      <button type="button" disabled={pending} onClick={() => change(replayed ? "Resetting replay run…" : "Resetting: waiting for the owner reset runner (up to 25 s)…",
        () => api.resetDemo(csrfToken),
        (view) => `Run ${view.run_id} ready${view.previous_run_id ? `, replacing ${view.previous_run_id}` : ""}. Detector ON.`)}>Reset run</button>
      <div role="group" aria-label="Forensics detector">
        {([true, false] as const).map((enabled) =>
          <button key={String(enabled)} type="button" aria-pressed={detectorPressed(control, enabled)} disabled={pending}
            onClick={() => change(`Turning forensics detector ${enabled ? "ON" : "OFF"}…`, () => api.setDetector(enabled, csrfToken),
              () => enabled ? "Forensics detector ON for new scenarios." : "Forensics detector OFF for new scenarios. Payee remains locked.")}>
            Forensics detector {enabled ? "ON" : "OFF"}</button>)}
      </div>
    </div>
    <div className="studio-toolbar">
      {(["S1", "S2", "S3", "S4"] as const).map((scenario) =>
        <button key={scenario} type="button" disabled={pending}
          onClick={() => void run(`Running ${scenario}…`, async () => {
            setReceipt(undefined);
            setReceipt(await api.runScenario(scenario, csrfToken));
            return `${scenario} admitted. Open the case for server results.`;
          })}>Run {scenario}</button>)}
    </div>
    {control && <p>{controlSummary(control, stale)}</p>}
    {loadError && !control && <p>Detector state unavailable: {loadError}</p>}
    <p role="status">{runState.pending || runState.message}</p>
    <p role="alert">{runState.failure}</p>
    {receipt && <p><Link href={`/cases/${encodeURIComponent(receipt.execution_id)}`}>Open scenario case</Link>
      {receipt.duplicate_of_execution_id && <> · <Link href={`/cases/${encodeURIComponent(receipt.duplicate_of_execution_id)}`}>Open original execution</Link></>}</p>}
  </section>;
}
