"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { APIError, api } from "@/lib/client";
import {
  REASON_MAX, displayTime, initialPortal, loadErrorMessage, portalReducer, recheckOnce, respondBlock, respondToNotice,
  type NoticeAction, type RespondOutcome, type Row,
} from "@/lib/vendor-portal";
import { useSession } from "../session";

export default function VendorPortal() {
  const { operator, busy, refresh } = useSession();
  const [state, dispatch] = useReducer(portalReducer, initialPortal);
  const generation = useRef(0);
  const inFlight = useRef<AbortController | null>(null);
  const rechecked = useRef(false);
  const sessionKey = operator ? `${operator.operator_id}:${operator.csrf_token}` : "";

  const sessionExpired = useCallback(() => { if (recheckOnce(rechecked)) void refresh(); }, [refresh]);

  const load = useCallback(async (): Promise<boolean> => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    const current = ++generation.current;
    dispatch({ type: "load", generation: current });
    try {
      const invoices = await api.vendorInvoices(controller.signal);
      dispatch({ type: "loaded", generation: current, invoices });
      rechecked.current = false;
      return true;
    } catch (error) {
      if (controller.signal.aborted) return false;
      dispatch({ type: "failed", generation: current, error: loadErrorMessage(error) });
      if (error instanceof APIError && error.status === 401) sessionExpired();
      return false;
    }
  }, [sessionExpired]);

  useEffect(() => {
    if (!sessionKey) return;
    void load();
    return () => inFlight.current?.abort();
  }, [sessionKey, load]);

  const toolbarStatus = busy ? "Checking operator session…" : state.loading ? "Loading supplier invoices…" : `${state.rows.length} invoices loaded.`;
  return <>
    <section className="page-header" aria-labelledby="vendor-title">
      <p className="eyebrow">SUPPLIER / SIMULATOR</p>
      <h1 id="vendor-title">Supplier portal <span className="simulator-badge">Simulator</span></h1>
      <p>Simulated supplier view: invoices issued, PayPal status, payments received and short-pay notices. Operators sign in to play the supplier.</p>
    </section>
    {!operator ? <p role="status">{busy ? "Checking operator session…" : "Sign in above to open the supplier simulator."}</p> : <>
      <div className="studio-toolbar">
        <p role="status">{toolbarStatus}</p>
        <button type="button" aria-disabled={state.loading} onClick={() => { if (!state.loading) void load(); }}>Refresh invoices</button>
      </div>
      {state.error && <p role="alert">{state.error}</p>}
      {!state.loading && !state.error && state.rows.length === 0 && <section className="case-pane" aria-labelledby="vendor-empty-title">
        <h2 id="vendor-empty-title">Invoices issued</h2><p>No supplier invoices yet. Run a demo scenario to issue one.</p>
      </section>}
      {state.rows.map((row) => <InvoiceCard key={row.id} row={row} csrfToken={operator.csrf_token} reload={load} onSessionExpired={sessionExpired} />)}
    </>}
  </>;
}

function When({ iso }: { iso: string }) {
  const text = displayTime(iso);
  return text ? <time dateTime={iso}>{text}</time> : <>an unknown time</>;
}

type CardProps = { row: Row; csrfToken: string; reload: () => Promise<boolean>; onSessionExpired: () => void };

function InvoiceCard({ row, csrfToken, reload, onSessionExpired }: CardProps) {
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<RespondOutcome | null>(null);
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const notice = row.notice;
  const ids = { title: `invoice-${row.id}`, reason: `reason-${row.id}`, help: `reason-help-${row.id}`, text: `notice-text-${row.id}` };

  async function respond(action: NoticeAction) {
    if (!notice || sending || lock.current) return;
    const blocked = respondBlock(action, reason);
    if (blocked) { setResult({ ok: false, message: blocked, sessionExpired: false }); return; }
    setSending(true); setResult(null);
    const outcome = await respondToNotice(notice.id, action, reason, csrfToken, reload, lock);
    setSending(false);
    if (!outcome) return;
    setResult(outcome);
    if (outcome.ok) setReason("");
    if (outcome.sessionExpired) onSessionExpired();
    heading.current?.focus(); // the buttons may be gone once the response is recorded
  }

  const contestBlock = respondBlock("CONTEST", reason);
  return <section className="case-pane" aria-labelledby={ids.title}>
    <h2 id={ids.title} ref={heading} tabIndex={-1}>Invoice {row.number}</h2>
    <dl>
      <dt>Supplier</dt><dd>{row.vendor}</dd>
      <dt>PayPal invoice</dt><dd><code>{row.id}</code></dd>
      <dt>PayPal status</dt><dd>{row.paypalStatus}</dd>
      <dt>Invoice total</dt><dd>{row.billed}</dd>
      <dt>Payments received</dt><dd>{row.received}</dd>
    </dl>
    <p role="status" className={result ? "local-status" : undefined}>{result?.message ?? ""}</p>
    <h3>Short-pay notice</h3>
    {!notice ? <p className="muted">No short-pay notice for this invoice.</p> : <>
      <p><span className="case-badge">{notice.statusLabel}</span> · Short by {notice.shortfall} · Sent <When iso={notice.sentAt} />
        {notice.respondedAt && <> · Answered <When iso={notice.respondedAt} /></>}</p>
      <p id={ids.text} className="muted">Notice text (generated)</p>
      <p className="notice-text" role="note" aria-labelledby={ids.text}>{notice.text}</p>
      {notice.canRespond ? <form className="decision-form" onSubmit={(event) => event.preventDefault()}>
        <label htmlFor={ids.reason}>Reason (required to contest)</label>
        <textarea id={ids.reason} value={reason} maxLength={REASON_MAX} rows={3} readOnly={sending}
          aria-describedby={ids.help} onChange={(event) => setReason(event.target.value)} />
        <p id={ids.help} className="muted">{sending ? "Sending your response…" : contestBlock ?? "One response only. It cannot be changed afterwards."}</p>
        <div>
          <button type="button" aria-disabled={sending} onClick={() => void respond("ACCEPT")}>Accept short payment</button>{" "}
          <button type="button" aria-disabled={sending || Boolean(contestBlock)} aria-describedby={ids.help} onClick={() => void respond("CONTEST")}>Contest</button>
        </div>
      </form> : <p className="muted">This notice has been answered. Only one response is allowed.</p>}
    </>}
  </section>;
}
