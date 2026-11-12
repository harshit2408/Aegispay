"use client";

import Link from "next/link";
import type { AgWidgetData, AgWidgetDataFormat, AgWidgetParams } from "ag-studio";
import type { components } from "../../lib/api";
import { usd } from "../../lib/case-display";

export interface AnalystData extends AgWidgetData<Record<string, never>, AgWidgetDataFormat<unknown>> { type: "analyst-snapshot" }

export function AnalystWidget({ context }: AgWidgetParams<AnalystData, unknown, { analystResult?: components["schemas"]["AnalystWidget"] }>) {
  const result = context.analystResult;
  return <section className="cockpit-widget" aria-label="Treasury Analyst result">
    <h2>Treasury Analyst</h2>
    {!result ? <p>Ask the Treasury Analyst to build an execution list or status summary. Sign in as a controller or CFO.</p> : <>
      {result.replayed && <div className="replay-banner">REPLAY · Includes recorded executions · No live settlement proof</div>}
      <p>Read-only snapshot · Last {result.days} days · {result.status ?? "All statuses"}<br /><time dateTime={result.as_of}>{result.as_of}</time></p>
      {result.kind === "status_summary" ? <table><caption>Execution counts by status</caption>
        <thead><tr><th scope="col">Status</th><th scope="col">Count</th></tr></thead>
        <tbody>{result.counts.map((entry) => <tr key={entry.status}><th scope="row">{entry.status}</th><td>{entry.count}</td></tr>)}</tbody>
      </table> : <div className="ledger-scroll" role="region" aria-label="Analyst execution list; scroll horizontally" tabIndex={0}>
        <table><caption>Server-selected executions</caption><thead><tr><th scope="col">Execution</th><th scope="col">Vendor</th><th scope="col">Status</th><th scope="col">Billed</th><th scope="col">Approved</th></tr></thead>
          <tbody>{result.rows.map((row) => <tr key={row.execution_id}><th scope="row"><Link href={`/cases/${encodeURIComponent(row.execution_id)}`}>{row.execution_id}</Link>{row.replayed && <span className="case-badge warning">REPLAY</span>}</th>
            <td>{row.vendor_name ?? "Unavailable"}</td><td>{row.status}</td><td>{row.billed_cents === null ? "Unavailable" : usd(row.billed_cents)}</td><td>{row.payable_cents === null ? "Unavailable" : usd(row.payable_cents)}</td></tr>)}</tbody>
        </table>
      </div>}
      {result.truncated && <p>Showing the latest {result.rows.length} rows. Counts cover the full selected window.</p>}
      {!result.rows.length && !result.counts.length && <p>No executions in this window.</p>}
      <p className="muted">Server values only. This widget cannot approve, pay or change records.</p>
    </>}
  </section>;
}
