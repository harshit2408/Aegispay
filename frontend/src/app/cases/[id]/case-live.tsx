"use client";

import Link from "next/link";
import { useEffect, useReducer, useState } from "react";
import { caseReadState, decisionBlock, isEligible } from "@/lib/case-display";
import { decideCase } from "@/lib/case-decision";
import { watchCase } from "@/lib/case-stream";
import { APIError } from "@/lib/client";
import type { components } from "@/lib/api";
import { useSession } from "../../session";
import { CaseDetails } from "./case-preview";

type Session = components["schemas"]["SessionView"];

export function CaseLive({ executionId }: { executionId: string }) {
  const { operator, busy } = useSession();
  if (!operator || busy) return <p role="status">{busy ? "Checking operator session…" : "Sign in above to view this case."}</p>;
  return <CurrentCase key={`${operator.operator_id}:${operator.csrf_token}`} executionId={executionId} operator={operator} />;
}

function CurrentCase({ executionId, operator }: { executionId: string; operator: Session }) {
  const [state, dispatch] = useReducer(caseReadState, { loading: true, replayed: false });
  const [attempt, setAttempt] = useState(0);
  const [stream, setStream] = useState({ message: "Connecting to updates…", closed: false });
  const [decisionMessage, setDecisionMessage] = useState("");

  useEffect(
    () => watchCase(executionId, dispatch, (message, closed = false) => setStream({ message, closed })),
    [executionId, attempt]);

  function refresh() { dispatch({ type: "loading" }); setAttempt((value) => value + 1); }
  const retry = <button type="button" onClick={() => {
    setStream({ message: "Connecting to updates…", closed: false });
    setAttempt((value) => value + 1);
  }}>Retry</button>;
  const stale = Boolean(state.data && state.error);

  return <>
    {state.replayed && <div className="replay-banner">REPLAY · Recorded case data</div>}
    {/* One stable polite live region: it only announces when its text changes. Retry stays in place to keep focus. */}
    <div role="status" className={stale ? "stale-banner" : undefined}>
      <p>{state.loading ? "Refreshing case…" : stale
        ? `Warning: showing the last loaded case data, which may be out of date. ${state.error}`
        : state.data ? stream.message : "Case unavailable"}</p>
      {(stale || (stream.closed && !state.error)) && retry}
    </div>
    {decisionMessage && <p role="status" className="local-status">{decisionMessage}</p>}
    {state.error && !state.data && <section className="empty-state" aria-labelledby="case-error-title">
      <h2 id="case-error-title">Case could not be loaded</h2>
      <p role="alert">{state.error}</p>
      <p className="locked-payee">Payee source: vendor master (locked)</p>
      {retry} <Link href="/">Back to cockpit</Link>
    </section>}
    {state.data && state.data.viewer.operator_id === operator.operator_id && <CaseDetails data={state.data}
      decisionControls={<CaseActions key={state.data.gate?.gate_hash ?? "no-gate"} data={state.data} loading={state.loading || stale} refresh={refresh} message={setDecisionMessage} />} />}
  </>;
}

function CaseActions({ data, loading, refresh, message }: {
  data: components["schemas"]["CaseDetail"]; loading: boolean; refresh: () => void; message: (text: string) => void;
}) {
  const session = useSession();
  const [selected, setSelected] = useState<components["schemas"]["DecisionRequest"]["action"]>();
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const viewer = data.viewer;
  const operator = session.operator;
  if (!operator || operator.role !== viewer.role || !isEligible(viewer, data.uploaded_by)) return <p className="muted">Only a controller or CFO who did not upload this invoice can review.</p>;
  const gate = data.gate;
  const action = selected && gate?.allowed_actions.includes(selected) ? selected : gate?.allowed_actions[0];
  if (!gate || !action) return <p className="muted">The gate allows no decision actions in this state.</p>;
  const blocked = sending ? "Recording decision; awaiting current server facts…" : loading ? "Refreshing the case before review…"
    : decisionBlock(data, viewer, data.approvals, gate.gate_hash, action, reason);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (blocked || !operator || !action) return;
    setSending(true); message("");
    try {
      await decideCase(data, viewer, action, reason, operator.csrf_token, refresh);
      message("Decision recorded. Reviewing updated server facts before further action.");
    } catch (error) {
      message(error instanceof Error ? error.message : "Decision could not be confirmed. Review the refreshed case.");
      if (error instanceof APIError && [401, 403].includes(error.status)) void session.refresh();
    } finally { setSending(false); }
  }

  return <form className="decision-form" onSubmit={submit}>
    <p>Reviewing as <strong>{viewer.operator_id}</strong> · {viewer.role}</p>
    <label htmlFor="case-action">Gate-allowed action</label>
    <select id="case-action" value={action} onChange={(event) => setSelected(gate.allowed_actions.find((allowed) => allowed === event.target.value))} disabled={sending}>
      {gate.allowed_actions.map((allowed) => <option key={allowed} value={allowed}>{allowed === "SHORT_PAY" ? "Short-pay" : allowed === "APPROVE" ? "Approve" : "Reject"}</option>)}
    </select>
    <label htmlFor="case-reason">Reason · at least 10 characters</label>
    <textarea id="case-reason" required minLength={10} maxLength={4000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} disabled={sending} aria-describedby="case-decision-block" />
    <p id="case-decision-block" className="muted">{blocked ?? "Submit confirms the displayed gate hash. Amount and payee remain locked."}</p>
    <button type="submit" disabled={!!blocked} aria-describedby="case-decision-block">Confirm {action.toLowerCase().replace("_", "-")}</button>
  </form>;
}
