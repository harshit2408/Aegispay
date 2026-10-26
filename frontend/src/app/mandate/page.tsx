export default function MandateStudio() {
  return (
    <>
      <section className="page-header" aria-labelledby="mandate-title">
        <p className="eyebrow">POLICY / MANDATE STUDIO</p>
        <h1 id="mandate-title">Mandate studio</h1>
        <p>Review payment policy before it becomes active.</p>
      </section>
      <section className="empty-state" aria-labelledby="mandate-empty-title">
        <p className="status-label">No active policy loaded</p>
        <h2 id="mandate-empty-title">Policy authoring is not connected yet</h2>
        <p>The authenticated workflow will compile a draft, show its changes and require a different eligible operator to activate it.</p>
        <p>No mandate can be created or changed from this preview.</p>
      </section>
    </>
  );
}
