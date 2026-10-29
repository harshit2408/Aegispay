import { CasePreview } from "./case-preview";
import { CaseLive } from "./case-live";
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
      {id === "preview" || id === previewCase.execution_id ? <CasePreview /> : <CaseLive key={id} executionId={id} />}
    </>
  );
}
