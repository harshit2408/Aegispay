"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { components } from "@/lib/api";
import { usd } from "@/lib/case-display";
import { APIError, api } from "@/lib/client";
import { useSession } from "./session";

type Row = components["schemas"]["ExecutionRow"];
type Session = components["schemas"]["SessionView"];
type DocumentReceipt = components["schemas"]["DocumentReceipt"];

export function Inbox() {
  const { operator, busy } = useSession();
  return <section className="case-pane" aria-labelledby="inbox-title">
    <h2 id="inbox-title">Invoice inbox</h2>
    {operator && !busy
      ? <OperatorInbox key={`${operator.operator_id}:${operator.csrf_token}`} operator={operator} />
      : <p role="status">{busy ? "Checking operator session…" : "Sign in to open invoice cases."}</p>}
  </section>;
}

function OperatorInbox({ operator }: { operator: Session }) {
  const { refresh } = useSession();
  const [rows, setRows] = useState<Row[]>();
  const [revision, setRevision] = useState(0);
  const [listError, setListError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [upload, setUpload] = useState<DocumentReceipt>();
  const [uploading, setUploading] = useState(false);
  const canUpload = operator.role === "controller" || operator.role === "cfo";

  useEffect(() => {
    const controller = new AbortController();
    void api.listExecutions(controller.signal).then((result) => {
      if (!controller.signal.aborted) { setRows(result); setListError(""); }
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setRows(undefined);
      setListError(error instanceof Error ? error.message : "The inbox could not be loaded.");
      if (error instanceof APIError && error.status === 401) void refresh();
    });
    return () => controller.abort();
  }, [revision, refresh]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uploading || !canUpload) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get("file");
    const po = String(data.get("po_id") ?? "").trim();
    const invoice = String(data.get("paypal_invoice_id") ?? "").trim();
    setUpload(undefined);
    if (!(file instanceof File) || !file.size || file.size > 10 * 1024 * 1024 || !po || !invoice) {
      setUploadError("Choose a PDF up to 10 MB and enter both record IDs.");
      return;
    }
    data.set("po_id", po);
    data.set("paypal_invoice_id", invoice);
    setUploading(true);
    setUploadError("");
    try {
      setUpload(await api.uploadDocument(data, operator.csrf_token));
      setRevision((value) => value + 1);
      form.reset();
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Upload could not be confirmed. Check the inbox.");
      if (error instanceof APIError && error.status === 401) void refresh();
    } finally { setUploading(false); }
  }

  return <>
    {canUpload && <form className="decision-form" onSubmit={submit} aria-label="Upload an invoice">
      <label htmlFor="invoice-pdf">Invoice PDF · up to 10 MB</label>
      <input id="invoice-pdf" name="file" type="file" accept="application/pdf,.pdf" required disabled={uploading} />
      <label htmlFor="invoice-po">Purchase order ID</label>
      <input id="invoice-po" name="po_id" required maxLength={128} disabled={uploading} />
      <label htmlFor="paypal-invoice-id">PayPal invoice ID</label>
      <input id="paypal-invoice-id" name="paypal_invoice_id" required maxLength={128} disabled={uploading} />
      <button type="submit" disabled={uploading}>{uploading ? "Uploading…" : "Upload invoice"}</button>
    </form>}
    {uploadError && <p role="alert">{uploadError}</p>}
    {upload && <p role="status">
      {upload.duplicate_of_execution_id ? "Duplicate blocked. " : "Invoice received. "}
      <Link href={`/cases/${encodeURIComponent(upload.execution_id)}`}>Open case</Link>
      {upload.duplicate_of_execution_id && <> · <Link href={`/cases/${encodeURIComponent(upload.duplicate_of_execution_id)}`}>Open original execution</Link></>}
    </p>}
    <div className="studio-toolbar"><button type="button" onClick={() => setRevision((value) => value + 1)}>Refresh inbox</button></div>
    {listError ? <p role="alert">{listError}</p> : !rows ? <p role="status">Loading invoice cases…</p> : rows.length === 0 ? <p>No invoices received.</p>
      : <div className="table-scroll" tabIndex={0} role="region" aria-label="Invoice inbox table">
        <table className="payables-table">
          <caption>Case status and amounts from the decision service</caption>
          <thead><tr><th scope="col">Case</th><th scope="col">Vendor</th><th scope="col">Status</th><th scope="col">Billed</th><th scope="col">Payable</th><th scope="col">Risk</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.execution_id}>
            <th scope="row"><Link href={`/cases/${encodeURIComponent(row.execution_id)}`}>{row.execution_id}</Link>{row.replayed && <span className="ledger-secondary">REPLAY</span>}</th>
            <td>{row.vendor_name ?? "Unavailable"}</td><td>{row.status.replaceAll("_", " ")}</td>
            <td>{row.billed_cents == null ? "Unavailable" : usd(row.billed_cents)}</td>
            <td>{row.payable_cents == null ? "Unavailable" : usd(row.payable_cents)}</td>
            <td>{row.risk_score == null ? "Pending" : `${row.risk_score}/100`}</td>
          </tr>)}</tbody>
        </table>
      </div>}
  </>;
}
