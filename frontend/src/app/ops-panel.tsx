"use client";

import { useEffect, useState } from "react";
import type { components } from "@/lib/api";
import { api } from "@/lib/client";
import { useSession } from "./session";

export function OpsPanel() {
  const { operator, busy } = useSession();
  if (!operator || busy || !["cfo", "admin"].includes(operator.role)) return null;
  return <OperatorOps key={`${operator.operator_id}:${operator.csrf_token}`} operator={operator} />;
}

function OperatorOps({ operator }: { operator: components["schemas"]["SessionView"] }) {
  const [data, setData] = useState<components["schemas"]["OpsStatusView"]>();
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api.opsStatus(controller.signal).then((result) => { if (!controller.signal.aborted) { setData(result); setError(""); } })
      .catch((error: unknown) => { if (!controller.signal.aborted) { setData(undefined); setError(error instanceof Error ? error.message : "Operations status unavailable."); } });
    return () => controller.abort();
  }, [attempt]);
  async function changeHalt(halted: boolean) {
    if (pending || reason.trim().length < 10) return;
    setPending(true); setError(""); setMessage("");
    try { await api.setHalt(halted, reason.trim(), operator.csrf_token); setReason(""); setMessage("Control updated. Reloading server status."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Halt change could not be confirmed. Refresh status."); }
    finally { setData(undefined); setAttempt((value) => value + 1); setPending(false); }
  }
  return <section className="case-pane" aria-label="Operations controls" aria-busy={pending}>
    <h2>Operations</h2>
    <button type="button" disabled={pending} onClick={() => setAttempt((value) => value + 1)}>Refresh operations</button>
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {data ? <>
      <p role="status">Settlement {data.halted ? "HALTED" : "running"} · {data.halted_reason ?? "No halt reason"}</p>
      <p>Velocity breaker: {data.velocity_breaker_tripped ? "TRIPPED" : "Not tripped"}</p>
      <p>Last published audit anchor: {data.last_anchor_at ?? "Not available"}</p>
      <label htmlFor="halt-reason">Halt or resume reason · 10–200 characters</label>
      <textarea id="halt-reason" minLength={10} maxLength={200} value={reason} onChange={(event) => setReason(event.target.value)} disabled={pending} />
      <div className="studio-toolbar">
        <button type="button" disabled={pending || reason.trim().length < 10} onClick={() => void changeHalt(true)}>Halt settlement</button>
        {operator.role === "cfo" ? <button type="button" disabled={pending || !data.halted || reason.trim().length < 10} onClick={() => void changeHalt(false)}>Resume settlement</button> : <p>Only the CFO may resume settlement.</p>}
      </div>
      <h3>Recent alerts</h3>
      {data.alerts.length ? <ul>{data.alerts.map((alert) => <li key={alert.alert_id}><strong>{alert.event}</strong> · {alert.created_at}
        {alert.execution_id && <> · Case <code>{alert.execution_id}</code></>} · {alert.delivered_at ? "Delivered" : "Delivery pending"}</li>)}</ul> : <p>No recent alerts.</p>}
      <h3>Vendor change queue</h3>
      {data.vendor_changes.length ? <ul>{data.vendor_changes.map((change) => <li key={change.change_id}>
        <strong>{change.vendor_id} · {change.status}</strong><br />Requested by {change.requested_by} · Approved by {change.approved_by ?? "Pending"}<br />
        Effective at {change.effective_at ?? "Pending approval"}. {change.effective_at && <CoolingCountdown effectiveAt={change.effective_at} />} Server enforces cooling before payment.
      </li>)}</ul> : <p>No pending vendor changes.</p>}
    </> : !error && <p role="status">Loading operations status…</p>}
  </section>;
}

function CoolingCountdown({ effectiveAt }: { effectiveAt: string }) {
  const [seconds, setSeconds] = useState<number>();
  useEffect(() => {
    const update = () => setSeconds(Math.max(0, Math.ceil((Date.parse(effectiveAt) - Date.now()) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [effectiveAt]);
  return <span>{seconds === undefined ? "Checking cooling time." : seconds > 0 ? `${seconds} seconds cooling remaining.` : "Cooling time elapsed; awaiting server activation."}</span>;
}
