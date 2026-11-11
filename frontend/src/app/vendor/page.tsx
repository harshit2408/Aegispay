import { previewCase } from "@/fixtures/case-preview";
import { usd } from "@/lib/case-display";

export default function VendorPortal() {
  return (
    <>
      <section className="page-header" aria-labelledby="vendor-title">
        <p className="eyebrow">SUPPLIER / SIMULATOR</p>
        <h1 id="vendor-title">Supplier portal <span className="simulator-badge">Simulator</span></h1>
        <p>The supplier side of the invoice and payment story.</p>
      </section>
      <section className="case-pane" aria-labelledby="vendor-empty-title">
        <p className="fixture-notice">SYNTHETIC SUPPLIER PREVIEW · No live supplier data or payment is loaded.</p>
        <h2 id="vendor-empty-title">Invoices issued</h2>
        <p>Invoice <code>{previewCase.paypal_invoice?.invoice_id}</code> · {previewCase.paypal_invoice?.status}</p>
        <p>Invoice total: {previewCase.paypal_invoice ? usd(previewCase.paypal_invoice.total_cents) : "Unavailable"}</p>
        <h3>Payments received</h3><p>No confirmed payment receipt in this synthetic preview.</p>
        <h3>Short-pay notice</h3>
        <p>Not connected. Notices and supplier responses require the supplier service; this preview never invents a delivered notice.</p>
        <button type="button" disabled aria-describedby="supplier-action-status">Accept</button>{" "}
        <button type="button" disabled aria-describedby="supplier-action-status">Contest</button>
        <p id="supplier-action-status" className="muted">Supplier accept/contest is unavailable until the authenticated vendor notice flow is connected.</p>
      </section>
    </>
  );
}
