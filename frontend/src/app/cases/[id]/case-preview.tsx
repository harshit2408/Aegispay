"use client";

import Link from "next/link";
import type { components } from "@/lib/api";
import { currentApprovals, usd } from "@/lib/case-display";
import { previewCase } from "@/fixtures/case-preview";
import { SettlementTimeline } from "../../execution-progress";

type CaseDetail = components["schemas"]["CaseDetail"];

function InvoiceRender({ data }: { data: CaseDetail }) {
  return <figure className="invoice-render">
    <svg viewBox="0 0 500 360" role="img" aria-label="Synthetic invoice render with hidden-text bounding box on page 1">
      <rect width="500" height="360" fill="#fff" />
      <g fill="#152031" fontFamily="system-ui, sans-serif">
        <text x="36" y="44" fontSize="22" fontWeight="700">INVOICE · SYNTHETIC</text>
        <text x="36" y="78" fontSize="15">Northstar Office · INV-1043</text>
        <text x="36" y="108" fontSize="15">Purchase order: PO-1043</text>
        <text x="36" y="156" fontSize="16">100 units × $5.00</text>
        <text x="36" y="192" fontSize="20" fontWeight="700">Total: $500.00 USD</text>
        <text x="36" y="234" fontSize="14">Payee: redirect@example.invalid</text>
      </g>
      {data.forensics?.hidden_segments.map((segment, index) => <rect key={index}
        x={segment.bbox[0]} y={segment.bbox[1]} width={segment.bbox[2] - segment.bbox[0]}
        height={segment.bbox[3] - segment.bbox[1]} fill="#ffe1a3" fillOpacity="0.55" stroke="#9b260f" strokeWidth="3">
        <title>{`${segment.technique} · page ${segment.page} · ${segment.text}`}</title>
      </rect>)}
      <text x="42" y="301" fill="#752000" fontSize="12">Hidden text location · see quoted finding below</text>
    </svg>
    <figcaption>Untrusted invoice · Synthetic rendering, page 1. Amber overlays mark hidden-text bounds; technique and raw text appear in Security.</figcaption>
  </figure>;
}

export function CasePreview() {
  return <CaseDetails data={previewCase} preview decisionControls={<p className="muted">Synthetic fixture · Read-only. Sign in and open a real case to record decisions.</p>} />;
}

export function CaseDetails({ data, preview = false, decisionControls }: {
  data: CaseDetail; preview?: boolean; decisionControls: React.ReactNode;
}) {
  const { vendor, purchase_order: po, goods_receipt: receipt, paypal_invoice: paypal,
    po_consumption: consumption, extraction, forensics, match, risk, gate, approvals } = data;
  const accepted = currentApprovals(data, approvals);
  const highRisk = gate?.reasons.includes("HIGH_RISK");
  const duplicateOf = data.duplicate_of_execution_id ?? match?.duplicate_of ?? match?.fuzzy_duplicate_of;
  const billed = paypal?.total_cents ?? match?.billed_cents;
  return <>
    <div className="case-summary" aria-label="Case overview">
      <div><span className={`case-badge${highRisk ? " danger" : ""}`}>{highRisk ? "HIGH RISK · " : ""}{data.status}</span><h2>{vendor?.name ?? "Invoice case"}</h2><p>{[extraction?.vendor_invoice_number, po && `${po.po_id} v${po.version}`, data.execution_id].filter(Boolean).join(" · ")}</p></div>
      <dl className="case-metrics">
        <div><dt>Billed · PayPal invoice</dt><dd>{billed == null ? "Unavailable" : usd(billed)}</dd></div>
        <div><dt>Gate payable · locked</dt><dd>{gate ? usd(gate.payable_cents) : "Unavailable"}</dd></div>
        <div><dt>Risk score</dt><dd>{risk ? `${risk.score} / 100` : "Pending"}</dd></div>
      </dl>
    </div>
    {duplicateOf && <p className="local-status">{data.status === "DUPLICATE_BLOCKED" ? "Duplicate blocked." : "Related invoice found."} <Link href={`/cases/${encodeURIComponent(duplicateOf)}`}>Open original execution</Link></p>}
    {preview && <p className="fixture-notice">SYNTHETIC CASE · Combined short-shipment / injection fixture for pane review. All records, people, IDs and findings below are fabricated. No live case is loaded.</p>}
    <div className="case-grid">
      <section className="case-pane" aria-labelledby="evidence-title">
        <p className="eyebrow">01 / SOURCE RECORDS</p><h2 id="evidence-title">Evidence</h2>
        <div className="source-grid">
          <article><h3>Purchase order <span className="case-badge">Trusted record</span></h3>{po ? <><p>{po.po_id} · v{po.version}</p><p>{po.description}<br />{po.ordered_qty} ordered · {usd(po.unit_price_cents)} / unit</p></> : <p>Unavailable</p>}</article>
          <article><h3>Goods receipt <span className="case-badge">Trusted record</span></h3>{receipt ? <><p>{receipt.grn_id} · v{receipt.version}</p><p><strong>{receipt.accepted_qty} accepted</strong><br />Recorded by {receipt.recorded_by}</p></> : <p>Unavailable</p>}</article>
        </div>
        <h3>Invoice PDF <span className="case-badge warning">Untrusted</span></h3>
        {preview ? <InvoiceRender data={data} /> : <p className="muted">Original PDF rendering is unavailable. Document text and findings below are untrusted evidence.</p>}
        <h3>PayPal invoice <span className="case-badge warning">Vendor-asserted</span></h3>
        {paypal ? <p><code>{paypal.invoice_id}</code> · {paypal.status}</p> : <p>Unavailable</p>}
        {match && <div className="table-scroll"><table><caption>Field comparison · PDF is a cross-check only</caption><thead><tr><th scope="col">Field</th><th scope="col">Invoice</th><th scope="col">Trusted record</th></tr></thead><tbody>
          <tr className={match.billed_qty !== match.accepted_qty ? "mismatch" : undefined}><th scope="row">Quantity</th><td>{match.billed_qty} billed</td><td>{match.accepted_qty} accepted</td></tr>
          <tr><th scope="row">Unit price</th><td>{usd(match.billed_unit_price_cents)}</td><td>{usd(match.po_unit_price_cents)}</td></tr>
          <tr className={!match.payee_matches_vendor_master ? "mismatch" : undefined}><th scope="row">Payee{!match.payee_matches_vendor_master ? " · mismatch" : ""}</th><td>{extraction?.payee_email ?? "Unavailable"}</td><td>{vendor ? `Vendor master v${vendor.version} (locked)` : "Unavailable"}</td></tr>
        </tbody></table></div>}
        <h3>PO consumption</h3>
        {consumption ? <><p>Accepted value {usd(consumption.accepted_value_cents)} · Already paid {usd(consumption.paid_cents)} · Reserved {usd(consumption.reserved_cents)}</p>
        {consumption.this_payable_cents == null ? <p>This gate payable: Unavailable</p> : <>
          <label htmlFor="po-consumption">This gate payable: {usd(consumption.this_payable_cents)} of accepted value</label>
          <meter id="po-consumption" min={0} max={Math.max(1, consumption.accepted_value_cents)} value={consumption.this_payable_cents}>{usd(consumption.this_payable_cents)}</meter>
        </>}
        <p className="muted">Values are supplied by {preview ? "the synthetic contract fixture" : "the server"}.</p></> : <p>Unavailable</p>}
      </section>

      <section className="case-pane" aria-labelledby="security-title">
        <p className="eyebrow">02 / TRUST BOUNDARY</p><h2 id="security-title">Security</h2>
        <h3>Raw hidden-text findings</h3>
        {forensics ? <>{forensics.hidden_segments.map((segment, index) => <article className="finding" key={index}>
          <p><span className="case-badge danger">{segment.technique}</span> Page {segment.page} · Bounds [{segment.bbox.join(", ")}]</p>
          <blockquote>{segment.text}</blockquote><p className="muted">Untrusted document text · displayed as inert evidence</p>
        </article>)}
        <p><strong>Detector {forensics.detector_enabled ? "ON" : "OFF"}</strong> · {forensics.detector_version}</p>
        <p><span className="case-badge danger">{forensics.classifier_label}</span> {forensics.classifier_rationale}</p></> : <p>No forensics report available.</p>}
        {risk && <div className="table-scroll"><table><caption>Risk contributions · deterministic assessment</caption><thead><tr><th scope="col">Finding</th><th scope="col">Points</th></tr></thead><tbody>{risk.contributions.map((item) => <tr key={item.finding}><th scope="row">{item.finding}</th><td>{item.points}</td></tr>)}</tbody></table></div>}
        <article className="payee-panel"><h3 className="locked-payee">Payee source: vendor master (locked)</h3>
          {vendor ? <dl><dt>Vendor master version</dt><dd>{vendor.version}</dd><dt>Approved payer ID</dt><dd><code>{vendor.paypal_payer_id}</code></dd><dt>Extracted payee · untrusted{match && !match.payee_matches_vendor_master ? " · mismatch" : ""}</dt><dd>{extraction?.payee_email ?? "Unavailable"}</dd><dt>Onboarded</dt><dd>{vendor.onboarded_at}</dd><dt>Last change requested by</dt><dd>{vendor.change_requested_by}</dd><dt>Approved by</dt><dd>{vendor.change_approved_by}</dd><dt>Effective</dt><dd>{vendor.change_effective_at}</dd></dl> : <p>Vendor master evidence unavailable.</p>}
        </article>
        <h3>Investigator facts</h3>
        {data.evidence?.facts.map((fact) => <article className="quoted-fact" key={fact.tool_call_id}><p>{fact.claim} {fact.vendor_asserted && <span className="case-badge warning">Vendor-asserted</span>}</p><blockquote>{fact.excerpt}</blockquote><p className="muted">Tool-call ID: <code>{fact.tool_call_id}</code></p></article>)}
        {!highRisk && data.evidence && <p>Advisory recommendation: {data.evidence.advisory_recommendation}</p>}
      </section>

      <section className="case-pane money-pane" aria-labelledby="money-title">
        <p className="eyebrow">03 / DECISION & RECEIPT</p><h2 id="money-title">Money</h2>
        <div className="money-grid"><div>
          {gate ? <><h3>Gate · {gate.route}</h3><ul>{gate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          <p>Policy {gate.policy_id} v{gate.policy_version} · Payable <strong>{usd(gate.payable_cents)}</strong></p>
          <p>Displayed gate hash: <code className="gate-hash">{gate.gate_hash}</code></p></> : <p>No gate decision available.</p>}
          {data.short_pay && <><div className="table-scroll"><table><caption>Short-pay model · supplied estimates, not a payment authorization</caption><thead><tr><th scope="col">Option</th><th scope="col">Expected cost</th></tr></thead><tbody><tr><th scope="row">Pay and claim</th><td>{usd(data.short_pay.expected_cost_pay_and_claim_cents)}</td></tr><tr><th scope="row">Short-pay + notice</th><td>{usd(data.short_pay.expected_cost_short_pay_cents)}</td></tr></tbody></table></div>
          <p>Rejected portion: {usd(data.short_pay.delta_cents)} · Model choice: {data.short_pay.chosen}. {gate && `Gate route: ${gate.route}.`}</p>
          <details><summary>Model assumptions · editable only through a mandate</summary><dl className="assumptions"><dt>Recovery probability</dt><dd>{data.short_pay.assumptions.p_recover}</dd><dt>Claim operations cost</dt><dd>{usd(data.short_pay.assumptions.claim_ops_cost_cents)}</dd><dt>Annual cost of capital</dt><dd>{data.short_pay.assumptions.annual_cost_of_capital}</dd><dt>Claim days</dt><dd>{data.short_pay.assumptions.claim_days}</dd><dt>Notice operations cost</dt><dd>{usd(data.short_pay.assumptions.notice_ops_cost_cents)}</dd><dt>Vendor contest probability</dt><dd>{data.short_pay.assumptions.p_vendor_contests}</dd><dt>Contest cost</dt><dd>{usd(data.short_pay.assumptions.contest_cost_cents)}</dd></dl></details></>}
          <SettlementTimeline entries={data.settlement_timeline} />
          <p>Invoice ID: <code>{paypal?.invoice_id ?? "Unavailable"}</code><br />Payout references appear with their settlement events above.</p>
          <button type="button" disabled aria-describedby="audit-unavailable">Export audit</button><p id="audit-unavailable" className="muted">{data.audit_anchor.verified ? "Server reports an anchored audit chain. Authenticated export is not connected." : "No externally anchored audit chain has been verified."}</p>
        </div><div>
          <h3>Approvals{gate && ` · ${accepted.length} of ${gate.required_approvals}`}</h3>
          <p>Approvals require distinct eligible humans bound to this gate. The uploader cannot approve.{preview && " This fixture is read-only."}</p>
          <ul className="approval-list">{approvals.map((approval) => <li key={approval.approval_id}>{approval.void_reason ? <><s>{approval.actor.operator_id} · {approval.action}</s><p className="warning-text">Voided: {approval.void_reason}</p></> : <><strong>{approval.actor.operator_id} · {approval.action}</strong><p>{approval.reason}</p></>}</li>)}</ul>
          {decisionControls}
        </div></div>
      </section>
    </div>
  </>;
}
