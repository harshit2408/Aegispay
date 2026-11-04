"use client";

import { useCallback, useEffect, useState } from "react";
import type { components } from "@/lib/api";
import { APIError, api } from "@/lib/client";
import { usd } from "@/lib/case-display";
import { useSession } from "../session";

type MandateRecord = components["schemas"]["MandateRecord"];
type Active = components["schemas"]["ActiveMandateView"];

export default function MandateStudio() {
  const { operator, busy } = useSession();
  return <>
    <section className="page-header" aria-labelledby="mandate-title">
      <p className="eyebrow">POLICY / MANDATE STUDIO</p><h1 id="mandate-title">Mandate studio</h1>
      <p>Compile an inactive draft, review its changes, then ask a different CFO to activate it.</p>
    </section>
    {!operator || busy ? <p role="status">{busy ? "Checking operator session…" : "Sign in above to review mandates."}</p>
      : <MandateEditor key={`${operator.operator_id}:${operator.csrf_token}`} />}
  </>;
}

function MandateEditor() {
  const session = useSession();
  const [active, setActive] = useState<Active | null>(null);
  const [records, setRecords] = useState<MandateRecord[]>([]);
  const [selection, setSelection] = useState("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const operator = session.operator;

  const load = useCallback((signal?: AbortSignal) => {
    return Promise.all([
        api.activeMandate(signal).catch((failure: unknown) => {
          if (failure instanceof APIError && failure.status === 404) return null;
          throw failure;
        }), api.listMandates(signal),
      ]).then(([current, versions]) => {
      if (!signal?.aborted) { setActive(current); setRecords(versions); }
    }).catch((failure: unknown) => {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : "Mandates could not be loaded.");
    }).finally(() => { if (!signal?.aborted) setLoading(false); });
  }, []);

  async function reload() {
    setLoading(true); setRecords([]); setActive(null); setError(""); setReason("");
    await load();
  }

  useEffect(() => {
    if (!operator || session.busy) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [operator, session.busy, load]);

  const draft = records.find((record) => `${record.policy_id}:${record.version}` === selection);
  const activationBlock = !operator ? "Sign in to activate a draft." : sending || loading ? "Waiting for current server facts…"
    : operator.role !== "cfo" ? "A CFO must activate a draft."
    : !draft ? "Select and review an inactive draft."
    : draft.is_active ? "This version is already active."
    : draft.activated_at ? "This version has been retired."
    : draft.created_by === operator.operator_id ? "The author cannot activate their own draft. A different CFO must review it."
    : reason.trim().length < 10 ? "Enter an activation reason of at least 10 characters." : null;

  async function compile(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!operator || operator.role !== "controller" || sending || !text.trim()) return;
    setSending(true); setMessage(""); setError("");
    try {
      const record = await api.compileMandate(text.trim(), operator.csrf_token);
      setSelection(`${record.policy_id}:${record.version}`); setReason("");
      setMessage("Inactive draft compiled. Review the server diff and clamps; a different CFO must activate it.");
    } catch (failure) {
      setMessage(failure instanceof Error ? failure.message : "Draft compilation failed.");
      if (failure instanceof APIError && [401, 403].includes(failure.status)) void session.refresh();
    } finally { await reload(); setSending(false); }
  }

  async function activate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (activationBlock || !draft || !operator) return;
    setSending(true); setMessage("");
    try {
      await api.activateMandate(draft.policy_id, draft.version, reason.trim(), operator.csrf_token);
      setMessage("Activation recorded. Active policy and spend reloaded from the server.");
    } catch (failure) {
      setMessage(failure instanceof APIError && failure.status === 409
        ? "Policy changed, review the refreshed draft and diff before activating again."
        : failure instanceof Error ? failure.message : "Activation could not be confirmed. Review current server state.");
      if (failure instanceof APIError && [401, 403].includes(failure.status)) void session.refresh();
    } finally { setReason(""); await reload(); setSending(false); }
  }

  return <>
    {message && <p role="status" className="local-status">{message}</p>}
    {!operator || session.busy ? <p role="status">{session.busy ? "Checking operator session…" : "Sign in above to review mandates."}</p> : <>
      <div className="studio-toolbar"><p role="status">{loading ? "Loading current policies…" : `${records.length} policy versions loaded.`}</p><button type="button" disabled={loading || sending} onClick={() => void reload()}>Refresh policies</button></div>
      {error && <p role="alert">{error}</p>}
      <div className="mandate-grid">
        <section className="case-pane" aria-labelledby="active-title"><h2 id="active-title">Active mandate</h2>
          {loading ? <p>Loading…</p> : active ? <><p><strong>{active.policy_id} v{active.version}</strong></p><p>Daily committed: {usd(active.committed_today_cents)} · Cap: {usd(active.policy.daily_cap_cents)}</p><details><summary>Active policy · server values</summary><pre>{JSON.stringify(active.policy, null, 2)}</pre></details></> : <p>{error ? "Active policy unavailable." : "No active mandate."}</p>}
          <h3>Compile a draft</h3>
          {operator.role === "controller" ? <form className="decision-form" onSubmit={compile}>
            <label htmlFor="mandate-text">Describe the payment policy</label><textarea id="mandate-text" value={text} onChange={(event) => setText(event.target.value)} required maxLength={20000} rows={7} disabled={sending || loading} aria-describedby="compile-help" />
            <p id="compile-help" className="muted">The compiler clamps drafts to organization limits. Compilation does not activate policy.</p>
            <button type="submit" disabled={sending || loading || !text.trim()}>Compile inactive draft</button>
          </form> : <p className="muted">A controller authors drafts. Your {operator.role} session can review policy versions.</p>}
        </section>
        <section className="case-pane" aria-labelledby="draft-title"><h2 id="draft-title">Review &amp; activate</h2>
          <div className="decision-form"><label htmlFor="policy-version">Policy version</label><select id="policy-version" value={selection} onChange={(event) => { setSelection(event.target.value); setReason(""); }} disabled={loading || sending}>
            <option value="">Select a version to review</option>{records.map((record) => <option key={`${record.policy_id}:${record.version}`} value={`${record.policy_id}:${record.version}`}>{record.policy_id} v{record.version} · {record.is_active ? "ACTIVE" : record.activated_at ? "RETIRED" : "INACTIVE DRAFT"} · {record.created_by}</option>)}
          </select></div>
          {draft && <>
            <p><span className="case-badge">{draft.is_active ? "ACTIVE" : draft.activated_at ? "RETIRED" : "INACTIVE DRAFT"}</span> · Author: {draft.created_by}</p>
            <h3>Organization clamps</h3><p>{draft.clamped_fields.length ? draft.clamped_fields.join(", ") : "No fields clamped."}</p>
            <div className="table-scroll"><table><caption>Deterministic diff against the current active policy</caption><thead><tr><th scope="col">Field</th><th scope="col">Active</th><th scope="col">Draft</th></tr></thead><tbody>
              {Object.entries(draft.diff).map(([field, change]) => <tr key={field}><th scope="row">{field}</th><td><code>{JSON.stringify(change.before)}</code></td><td><code>{JSON.stringify(change.after)}</code></td></tr>)}
            </tbody></table>{!Object.keys(draft.diff).length && <p>No policy changes.</p>}</div>
            <details><summary>Full draft · read-only server values</summary><pre>{JSON.stringify(draft.policy, null, 2)}</pre></details>
          </>}
          <form className="decision-form" onSubmit={activate}>
            <label htmlFor="activation-reason">Activation reason · at least 10 characters</label><textarea id="activation-reason" value={reason} onChange={(event) => setReason(event.target.value)} required minLength={10} maxLength={1000} rows={3} disabled={sending || loading || operator.role !== "cfo" || !draft || draft.is_active || !!draft.activated_at || draft.created_by === operator.operator_id} aria-describedby="activation-block" />
            <p id="activation-block" className="muted">{activationBlock ?? "Activate this reviewed version as the current mandate."}</p><button type="submit" disabled={!!activationBlock} aria-describedby="activation-block">Activate reviewed draft</button>
          </form>
        </section>
      </div>
    </>}
  </>;
}
