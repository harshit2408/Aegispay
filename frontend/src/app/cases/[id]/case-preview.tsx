"use client";

import { useState } from "react";
import type { components } from "@/lib/api";
import { currentApprovals, decisionBlock, isEligible, usd } from "@/lib/case-display";
import { mockReviewers, previewCase } from "@/fixtures/case-preview";

type CaseDetail = components["schemas"]["CaseDetail"];
type Action = components["schemas"]["DecisionRequest"]["action"];
const actionLabels: Record<Action, string> = { APPROVE: "Approve", SHORT_PAY: "Short-pay", REJECT: "Reject" };

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
      {data.forensics.hidden_segments.map((segment, index) => <rect key={index}
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
  const [data, setData] = useState(previewCase);
  const [viewer, setViewer] = useState(previewCase.viewer);
  const [approvals, setApprovals] = useState(previewCase.approvals);
  const [displayedHash, setDisplayedHash] = useState(previewCase.gate.gate_hash);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("Local rehearsal only. Decisions are not stored or sent.");
  const [action, setAction] = useState<Action>(previewCase.gate.allowed_actions[0] ?? "APPROVE");
  const accepted = currentApprovals(data, approvals);
  const blocked = decisionBlock(data, viewer, approvals, displayedHash, action, reason);
  const highRisk = data.gate.reasons.includes("HIGH_RISK");
  const stale = displayedHash !== data.gate.gate_hash;

  function rehearse(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const error = decisionBlock(data, viewer, approvals, displayedHash, action, reason);
    if (error) { setMessage(error); return; }
    setApprovals([...approvals, { approval_id: `local-${approvals.length}`, actor: viewer, action,
      reason: reason.trim(), gate_hash: displayedHash, void_reason: null }]);
    setReason("");
    setMessage(`Local ${actionLabels[action].toLowerCase()} rehearsal recorded for ${viewer.operator_id}. No approval or payment was sent.`);
  }

  function changeCase() {
    setData({ ...data, gate: { ...data.gate, gate_hash: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" } });
    setApprovals(approvals.map((approval) => ({ ...approval,
      void_reason: approval.void_reason ?? "Case changed after review; this approval is void." })));
    setMessage("Case changed, review again. Earlier local approvals are void.");
  }

  return <>
    <div className="case-summary" aria-label="Synthetic case overview">
      <div><span className="case-badge danger">HIGH RISK · FROZEN</span><h2>{data.vendor.name}</h2><p>INV-1043 · PO-1043 v2 · {data.execution_id}</p></div>
      <dl className="case-metrics">
        <div><dt>Billed · PayPal invoice</dt><dd>{usd(data.match.billed_cents)}</dd></div>
        <div><dt>Gate payable · locked</dt><dd>{usd(data.gate.payable_cents)}</dd></div>
        <div><dt>Risk score</dt><dd>{data.risk.score} / 100</dd></div>
      </dl>
    </div>
    <p className="fixture-notice">SYNTHETIC CASE · Combined short-shipment / injection fixture for pane review. All records, people, IDs and findings below are fabricated. No live case is loaded.</p>
    <div className="case-grid">
      <section className="case-pane" aria-labelledby="evidence-title">
        <p className="eyebrow">01 / SOURCE RECORDS</p><h2 id="evidence-title">Evidence</h2>
        <div className="source-grid">
          <article><h3>Purchase order <span className="case-badge">Trusted record</span></h3><p>{data.purchase_order.po_id} · v{data.purchase_order.version}</p><p>{data.purchase_order.description}<br />{data.purchase_order.ordered_qty} ordered · {usd(data.purchase_order.unit_price_cents)} / unit</p></article>
          <article><h3>Goods receipt <span className="case-badge">Trusted record</span></h3><p>{data.goods_receipt.grn_id} · v{data.goods_receipt.version}</p><p><strong>{data.goods_receipt.accepted_qty} accepted</strong><br />Recorded by {data.goods_receipt.recorded_by}</p></article>
        </div>
        <h3>Invoice PDF <span className="case-badge warning">Untrusted</span></h3>
        <InvoiceRender data={data} />
        <h3>PayPal invoice <span className="case-badge warning">Vendor-asserted</span></h3>
        <p><code>{data.paypal_invoice.invoice_id}</code> · {data.paypal_invoice.status}</p>
        <div className="table-scroll"><table><caption>Field comparison · PDF is a cross-check only</caption><thead><tr><th scope="col">Field</th><th scope="col">Invoice</th><th scope="col">Trusted record</th></tr></thead><tbody>
          <tr className="mismatch"><th scope="row">Quantity · mismatch</th><td>{data.match.billed_qty} billed</td><td>{data.match.accepted_qty} accepted</td></tr>
          <tr><th scope="row">Unit price</th><td>{usd(data.match.billed_unit_price_cents)}</td><td>{usd(data.match.po_unit_price_cents)}</td></tr>
          <tr className="mismatch"><th scope="row">Payee · mismatch</th><td>{data.extraction.payee_email}</td><td>Vendor master v{data.vendor.version} (locked)</td></tr>
        </tbody></table></div>
        <h3>PO consumption</h3>
        <p>Accepted value {usd(data.po_consumption.accepted_value_cents)} · Already paid {usd(data.po_consumption.paid_cents)} · Reserved {usd(data.po_consumption.reserved_cents)}</p>
        <label htmlFor="po-consumption">This gate payable: {usd(data.po_consumption.this_payable_cents)} of accepted value</label>
        <meter id="po-consumption" min={0} max={data.po_consumption.accepted_value_cents} value={data.po_consumption.this_payable_cents}>{usd(data.po_consumption.this_payable_cents)}</meter>
        <p className="muted">Values are supplied by the synthetic contract fixture. The browser does not calculate payment amounts.</p>
      </section>

      <section className="case-pane" aria-labelledby="security-title">
        <p className="eyebrow">02 / TRUST BOUNDARY</p><h2 id="security-title">Security</h2>
        <h3>Raw hidden-text findings</h3>
        {data.forensics.hidden_segments.map((segment, index) => <article className="finding" key={index}>
          <p><span className="case-badge danger">{segment.technique}</span> Page {segment.page} · Bounds [{segment.bbox.join(", ")}]</p>
          <blockquote>{segment.text}</blockquote><p className="muted">Untrusted document text · displayed as inert evidence</p>
        </article>)}
        <p><strong>Detector ON</strong> · {data.forensics.detector_version}</p>
        <p><span className="case-badge danger">{data.forensics.classifier_label}</span> {data.forensics.classifier_rationale}</p>
        <div className="table-scroll"><table><caption>Risk contributions · deterministic fixture</caption><thead><tr><th scope="col">Finding</th><th scope="col">Points</th></tr></thead><tbody>{data.risk.contributions.map((risk) => <tr key={risk.finding}><th scope="row">{risk.finding}</th><td>{risk.points}</td></tr>)}</tbody></table></div>
        <article className="payee-panel"><h3>Payee source: vendor master v{data.vendor.version} (locked)</h3>
          <p className="locked-payee">Payee source: vendor master (locked)</p>
          <dl><dt>Approved payer ID</dt><dd><code>{data.vendor.paypal_payer_id}</code></dd><dt>Extracted payee · untrusted · mismatch</dt><dd>{data.extraction.payee_email}</dd><dt>Onboarded</dt><dd>{data.vendor.onboarded_at}</dd><dt>Last change requested by</dt><dd>{data.vendor.change_requested_by}</dd><dt>Approved by</dt><dd>{data.vendor.change_approved_by}</dd><dt>Effective</dt><dd>{data.vendor.change_effective_at}</dd></dl>
        </article>
        <h3>Investigator facts</h3>
        {data.evidence?.facts.map((fact) => <article className="quoted-fact" key={fact.tool_call_id}><p>{fact.claim} {fact.vendor_asserted && <span className="case-badge warning">Vendor-asserted</span>}</p><blockquote>{fact.excerpt}</blockquote><p className="muted">Tool-call ID: <code>{fact.tool_call_id}</code></p></article>)}
        {!highRisk && data.evidence && <p>Advisory recommendation: {data.evidence.advisory_recommendation}</p>}
      </section>

      <section className="case-pane money-pane" aria-labelledby="money-title">
        <p className="eyebrow">03 / DECISION & RECEIPT</p><h2 id="money-title">Money</h2>
        <div className="money-grid"><div>
          <h3>Gate · {data.gate.route}</h3><ul>{data.gate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          <p>Policy {data.gate.policy_id} v{data.gate.policy_version} · Payable <strong>{usd(data.gate.payable_cents)}</strong></p>
          <p>Displayed gate hash: <code className="gate-hash">{displayedHash}</code></p>
          {data.short_pay && <><div className="table-scroll"><table><caption>Short-pay model · supplied estimates, not a payment authorization</caption><thead><tr><th scope="col">Option</th><th scope="col">Expected cost</th></tr></thead><tbody><tr><th scope="row">Pay and claim</th><td>{usd(data.short_pay.expected_cost_pay_and_claim_cents)}</td></tr><tr><th scope="row">Short-pay + notice</th><td>{usd(data.short_pay.expected_cost_short_pay_cents)}</td></tr></tbody></table></div>
          <p>Rejected portion: {usd(data.short_pay.delta_cents)} · Model choice: {data.short_pay.chosen}. The HIGH_RISK gate remains frozen.</p>
          <details><summary>Model assumptions · editable only through a mandate</summary><dl className="assumptions"><dt>Recovery probability</dt><dd>{data.short_pay.assumptions.p_recover}</dd><dt>Claim operations cost</dt><dd>{usd(data.short_pay.assumptions.claim_ops_cost_cents)}</dd><dt>Annual cost of capital</dt><dd>{data.short_pay.assumptions.annual_cost_of_capital}</dd><dt>Claim days</dt><dd>{data.short_pay.assumptions.claim_days}</dd><dt>Notice operations cost</dt><dd>{usd(data.short_pay.assumptions.notice_ops_cost_cents)}</dd><dt>Vendor contest probability</dt><dd>{data.short_pay.assumptions.p_vendor_contests}</dd><dt>Contest cost</dt><dd>{usd(data.short_pay.assumptions.contest_cost_cents)}</dd></dl></details></>}
          <h3>Settlement timeline</h3>
          <ol className="settlement-timeline">{data.settlement_timeline.map((entry) => <li key={entry.status}><strong>{entry.status}</strong> · {entry.occurred_at ?? "Not reached"}<br /><span className="muted">{entry.simulated ? "Synthetic" : "Live"} · {entry.verified ? "GET verified" : "No verified receipt"} · {entry.paypal_id ?? "No payout ID"}</span></li>)}</ol>
          <p>Invoice ID: <code>{data.paypal_invoice.invoice_id}</code><br />Payout batch / item IDs: none · No payment sent</p>
          <button type="button" disabled aria-describedby="audit-unavailable">Export audit</button><p id="audit-unavailable" className="muted">Unavailable in this fixture. No externally anchored audit chain has been verified.</p>
        </div><div>
          <h3>Approvals · {accepted.length} of {data.gate.required_approvals}</h3>
          <p>HIGH_RISK requires two distinct eligible humans. The uploader cannot approve. This preview rehearses the controls locally.</p>
          <ul className="approval-list">{approvals.map((approval) => <li key={approval.approval_id}>{approval.void_reason ? <><s>{approval.actor.operator_id} · {approval.action}</s><p className="warning-text">Voided: {approval.void_reason}</p></> : <><strong>{approval.actor.operator_id} · {approval.action}</strong><p>{approval.reason}</p></>}</li>)}</ul>
          <form onSubmit={rehearse} className="decision-form">
            <label htmlFor="mock-reviewer">Synthetic reviewer · local role fixture</label>
            <select id="mock-reviewer" value={viewer.operator_id} onChange={(event) => setViewer(mockReviewers.find((operator) => operator.operator_id === event.target.value)!)}>{mockReviewers.map((operator) => <option key={operator.operator_id} value={operator.operator_id}>{operator.operator_id} ({operator.role}){operator.operator_id === data.uploaded_by ? " · uploader" : ""}</option>)}</select>
            {isEligible(viewer, data.uploaded_by) && <><label htmlFor="mock-action">Gate-allowed action</label><select id="mock-action" value={action} onChange={(event) => setAction(event.target.value as Action)}>{data.gate.allowed_actions.map((allowed) => <option key={allowed} value={allowed}>{actionLabels[allowed]}</option>)}</select></>}
            <label htmlFor="decision-reason">Reason · at least 10 characters</label><textarea id="decision-reason" value={reason} onChange={(event) => setReason(event.target.value)} required minLength={10} maxLength={4000} rows={3} aria-describedby="decision-block" />
            <p id="decision-block" className="muted">{blocked ?? "Ready to rehearse locally against the displayed gate hash."}</p>
            {isEligible(viewer, data.uploaded_by) && data.gate.allowed_actions.includes(action) && <button type="submit" disabled={!!blocked} aria-describedby="decision-block">Rehearse {actionLabels[action].toLowerCase()} locally</button>}
          </form>
          <div className="mock-controls"><button type="button" onClick={changeCase} disabled={stale || data.gate.gate_hash.startsWith("c")} aria-describedby="mock-change-help">Simulate case change</button>{stale && <button type="button" onClick={() => { setDisplayedHash(data.gate.gate_hash); setMessage("Refreshed gate reviewed locally. Earlier approvals remain void."); }}>Review refreshed gate</button>}</div>
          <p id="mock-change-help" className="muted">One simulated change per page load. Reload this page to restart the rehearsal.</p>
          <p role="status" className="local-status">{message}</p>
        </div></div>
      </section>
    </div>
  </>;
}
