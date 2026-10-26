export default function VendorPortal() {
  return (
    <>
      <section className="page-header" aria-labelledby="vendor-title">
        <p className="eyebrow">SUPPLIER / SIMULATOR</p>
        <h1 id="vendor-title">Supplier portal <span className="simulator-badge">Simulator</span></h1>
        <p>The supplier side of the invoice and payment story.</p>
      </section>
      <section className="empty-state" aria-labelledby="vendor-empty-title">
        <p className="status-label">No supplier invoices loaded</p>
        <h2 id="vendor-empty-title">The supplier simulator is not connected yet</h2>
        <p>Invoice status, payments received and short-pay notices will appear here when the simulator backend is connected.</p>
        <p>This area represents a simulated supplier. No supplier action or payment runs from this preview.</p>
      </section>
    </>
  );
}
