import Link from "next/link";
import { CasePreview } from "./case-preview";
import { previewCase } from "@/fixtures/case-preview";

export default async function CaseWorkspace({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <section className="page-header" aria-labelledby="case-title">
        <p className="eyebrow">PAYABLES / CASE WORKSPACE</p>
        <h1 id="case-title">Case workspace</h1>
        <p>Review the evidence, security findings and payment decision for one invoice.</p>
      </section>
      {id === "preview" || id === previewCase.execution_id ? <CasePreview /> : <section className="empty-state" aria-labelledby="case-empty-title">
        <p className="status-label">No case loaded</p>
        <h2 id="case-empty-title">Case details are not connected yet</h2>
        <p>Route reference: <code>{id}</code>. This preview has no invoice, evidence or payment decision attached.</p>
        <p className="locked-payee">Payee source: vendor master (locked)</p>
        <p>Payment actions remain unavailable until the backend supplies a decision and verifies operator eligibility.</p>
        <Link className="text-link" href="/">Return to the cockpit</Link>
      </section>}
    </>
  );
}
