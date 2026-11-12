"use client";

import { useCallback, useEffect, useState } from "react";
import type { components } from "@/lib/api";
import { APIError, api } from "@/lib/client";
import { REASON_MAX, respondBlock, respondToNotice, supplierRow, type NoticeAction } from "@/lib/vendor-portal";
import { useSession } from "../session";

type Row = ReturnType<typeof supplierRow>;

export default function VendorPortal() {
  const { operator, busy } = useSession();
  return <>
    <section className="page-header" aria-labelledby="vendor-title">
      <p className="eyebrow">SUPPLIER / SIMULATOR</p>
      <h1 id="vendor-title">Supplier portal <span className="simulator-badge">Simulator</span></h1>
      <p>Simulated supplier view: invoices issued, PayPal status, payments received and short-pay notices. Operators sign in to play the supplier.</p>
    </section>
    {!operator || busy ? <p role="status">{busy ? "Checking operator session…" : "Sign in above to open the supplier simulator."}</p>
      : <SupplierInvoices key={`${operator.operator_id}:${operator.csrf_token}`} />}
  </>;
}

function SupplierInvoices() {
  const { refresh } = useSession();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback((signal?: AbortSignal) => {
    return api.vendorInvoices(signal).then((invoices: components["schemas"]["SupplierInvoice"][]) => {
      if (!signal?.aborted) { setRows(invoices.map(supplierRow)); setError(""); }
    }).catch((failure: unknown) => {
      if (signal?.aborted) return;
      setRows([]);
      if (failure instanceof APIError && failure.status === 401) { setError("Session expired. Sign in again."); void refresh(); }
      else setError(failure instanceof Error ? failure.message : "Supplier invoices could not be loaded.");
    }).finally(() => { if (!signal?.aborted) setLoading(false); });
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return <>
    <p role="status" aria-live="polite" className="local-status">{message}</p>
    <div className="studio-toolbar">
      <p role="status">{loading ? "Loading supplier invoices…" : `${rows.length} invoices loaded.`}</p>
      <button type="button" disabled={loading} onClick={() => { setMessage(""); setLoading(true); void load(); }}>Refresh invoices</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {!loading && !error && rows.length === 0 && <section className="case-pane"><h2>Invoices issued</h2><p>No supplier invoices yet. Run a demo scenario to issue one.</p></section>}
    {rows.map((row) => <InvoiceCard key={row.id} row={row} onMessage={setMessage} reload={load} />)}
  </>;
}

function InvoiceCard({ row, onMessage, reload }: { row: Row; onMessage: (text: string) => void; reload: () => Promise<unknown> }) {
  const session = useSession();
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const notice = row.notice;
  const titleId = `invoice-${row.id}`;
  const reasonId = `reason-${row.id}`;
  const helpId = `reason-help-${row.id}`;

  async function respond(action: NoticeAction) {
    const operator = session.operator;
    if (!notice || !operator || sending) return;
    setSending(true); onMessage("");
    try {
      onMessage(await respondToNotice(notice.id, action, reason, operator.csrf_token, reload));
      setReason("");
    } catch (failure) {
      onMessage(failure instanceof Error ? failure.message : "The response could not be confirmed. Review the refreshed invoices.");
      if (failure instanceof APIError && [401, 403].includes(failure.status)) void session.refresh();
    } finally { setSending(false); }
  }

  const contestBlock = respondBlock("CONTEST", reason);
  return <section className="case-pane" aria-labelledby={titleId}>
    <h2 id={titleId}>Invoice {row.number}</h2>
    <dl>
      <dt>Supplier</dt><dd>{row.vendor}</dd>
      <dt>PayPal invoice</dt><dd><code>{row.id}</code></dd>
      <dt>PayPal status</dt><dd>{row.paypalStatus}</dd>
      <dt>Invoice total</dt><dd>{row.billed}</dd>
      <dt>Payments received</dt><dd>{row.received}</dd>
    </dl>
    <h3>Short-pay notice</h3>
    {!notice ? <p className="muted">No short-pay notice for this invoice.</p> : <>
      <p><span className="case-badge">{notice.statusLabel}</span> · Short by {notice.shortfall} · Sent {new Date(notice.sentAt).toLocaleString()}
        {notice.respondedAt && <> · Answered {new Date(notice.respondedAt).toLocaleString()}</>}</p>
      <blockquote style={{ whiteSpace: "pre-wrap" }}>{notice.text}</blockquote>
      {notice.canRespond ? <form className="decision-form" onSubmit={(event) => event.preventDefault()}>
        <label htmlFor={reasonId}>Reason (required to contest)</label>
        <textarea id={reasonId} value={reason} maxLength={REASON_MAX} rows={3} disabled={sending}
          aria-describedby={helpId} onChange={(event) => setReason(event.target.value)} />
        <p id={helpId} className="muted">{sending ? "Sending your response…" : contestBlock ?? "One response only. It cannot be changed afterwards."}</p>
        <div>
          <button type="button" disabled={sending} onClick={() => void respond("ACCEPT")}>Accept short payment</button>{" "}
          <button type="button" disabled={sending || Boolean(contestBlock)} aria-describedby={helpId} onClick={() => void respond("CONTEST")}>Contest</button>
        </div>
      </form> : <p className="muted">This notice has been answered. Only one response is allowed.</p>}
    </>}
  </section>;
}
