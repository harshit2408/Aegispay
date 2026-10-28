"use client";

import Link from "next/link";
import type { AgWidgetData, AgWidgetDataFormat, AgWidgetParams } from "ag-studio";
import type { components } from "../../lib/api";
import { usd } from "../../lib/case-display";
import { frozenExposure, gaugeValue, triad } from "../../lib/cockpit-display";

type CaseDetail = components["schemas"]["CaseDetail"];
type Policy = components["schemas"]["MandatePolicy"];

export interface CockpitContext {
  cases: readonly CaseDetail[];
  paidByExecution: Readonly<Record<string, number>>;
  policy: Policy | null;
  committedCents: number;
  businessDate: string;
}

export interface LedgerData extends AgWidgetData<Record<string, never>, AgWidgetDataFormat<unknown>> { type: "payables-ledger" }
export interface GaugeData extends AgWidgetData<Record<string, never>, AgWidgetDataFormat<unknown>> { type: "mandate-gauge" }
export interface ExposureData extends AgWidgetData<Record<string, never>, AgWidgetDataFormat<unknown>> { type: "frozen-exposure" }

export function PayablesLedger({ cases, paidByExecution }: Pick<CockpitContext, "cases" | "paidByExecution">) {
  return <section className="cockpit-widget" aria-label="Payables ledger">
    <h2>Payables ledger</h2>
    <p className="muted">PO · Receipt · Source. Open a case to review its evidence.</p>
    {cases.length === 0 ? <p>No payables to display.</p> : <div className="table-scroll" tabIndex={0}
      role="region" aria-label="Payables ledger table; scroll horizontally on narrow screens">
      <table className="payables-table">
        <caption>USD · Billed and paid amounts supplied by the execution record</caption>
        <thead><tr><th scope="col">Case / PO</th><th scope="col">Vendor</th><th scope="col">Match</th>
          <th scope="col">Billed → Paid</th><th scope="col">Risk</th><th scope="col">Status</th></tr></thead>
        <tbody>{cases.map((data) => {
          const match = triad(data);
          const href = `/cases/${encodeURIComponent(data.execution_id)}`;
          const paid = Object.hasOwn(paidByExecution, data.execution_id) ? paidByExecution[data.execution_id] : undefined;
          return <tr key={data.execution_id} onClick={(event) => {
            if (!(event.target as HTMLElement).closest("a")) event.currentTarget.querySelector("a")?.click();
          }}>
            <th scope="row"><Link href={href}>{data.extraction.vendor_invoice_number ?? data.execution_id}</Link>
              <span className="ledger-secondary">{data.purchase_order.po_id}</span></th>
            <td>{data.vendor.name}</td>
            <td><div className="triad-pills">{Object.entries(match).map(([name, state]) =>
              <span key={name} className={`case-badge ${["Matched", "Accepted"].includes(state) ? "success" : "warning"}`}>
                {name === "po" ? "PO" : name === "receipt" ? "Receipt" : "Source"}: {state}
              </span>)}</div></td>
            <td className="ledger-money">{usd(data.match.billed_cents)} → {paid === undefined ? "Unavailable" : usd(paid)}</td>
            <td><meter min={0} max={100} low={30} high={70} optimum={0} value={data.risk.score} aria-label={`Risk ${data.risk.score} of 100`} />
              <span className="ledger-secondary">{data.risk.score}/100</span></td>
            <td><span className="case-badge warning">{data.status.replaceAll("_", " ")}</span></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
  </section>;
}

export function MandateGauge({ policy, committedCents, businessDate }: Pick<CockpitContext, "policy" | "committedCents" | "businessDate">) {
  if (!policy) return <section className="cockpit-widget" aria-label="Mandate gauge"><h2>Mandate gauge</h2><p>No active mandate.</p></section>;
  const value = gaugeValue(committedCents, policy.daily_cap_cents);
  return <section className="cockpit-widget" aria-label="Mandate gauge">
    <h2>Mandate gauge</h2>
    <p className="muted">{policy.policy_id} · Active version {policy.version}<br />{businessDate} · {policy.cap_timezone}</p>
    <p className="gauge-amount">{usd(committedCents)} <span>committed</span></p>
    <p>Daily cap {usd(policy.daily_cap_cents)}</p>
    {value === null ? <p className="warning-text">Daily cap is zero. Auto-payment unavailable.</p> : <>
      <meter min={0} max={policy.daily_cap_cents} value={value}
        aria-label={`${usd(committedCents)} committed of ${usd(policy.daily_cap_cents)} daily cap`} />
      {committedCents > policy.daily_cap_cents && <p className="warning-text">Committed spend exceeds the daily cap.</p>}
    </>}
    <p className="muted">Committed includes reservations. The backend enforces spend limits.</p>
  </section>;
}

export function FrozenExposure({ cases }: Pick<CockpitContext, "cases">) {
  const groups = frozenExposure(cases);
  return <section className="cockpit-widget" aria-label="Exposure by reason">
    <h2>Exposure by reason</h2>
    <p className="muted">Frozen billed value · USD</p>
    {groups.length === 0 ? <p>No frozen exposure.</p> : <ul className="exposure-list">{groups.map((group) =>
      <li key={group.code}><span><strong>{group.code === "OTHER" ? "Other" : group.code}</strong> · {group.label}
        <span className="ledger-secondary">{group.count} {group.count === 1 ? "invoice" : "invoices"}</span></span>
        <strong>{usd(group.cents)}</strong></li>)}</ul>}
    <p className="muted">An invoice can appear under several reasons. Groups are not additive.</p>
  </section>;
}

export function LedgerWidget({ context }: AgWidgetParams<LedgerData, unknown, CockpitContext>) { return <PayablesLedger {...context} />; }
export function GaugeWidget({ context }: AgWidgetParams<GaugeData, unknown, CockpitContext>) { return <MandateGauge {...context} />; }
export function ExposureWidget({ context }: AgWidgetParams<ExposureData, unknown, CockpitContext>) { return <FrozenExposure {...context} />; }
