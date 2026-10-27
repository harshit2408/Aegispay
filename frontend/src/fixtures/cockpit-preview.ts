import { previewCase } from "./case-preview.ts";

// Synthetic read-model snapshot; paid amounts belong to executions, not the PO aggregate.
export const cockpitPreview = {
  cases: [previewCase],
  paidByExecution: { [previewCase.execution_id]: 0 } as Readonly<Record<string, number>>,
  policy: previewCase.policy,
  committedCents: 90500,
  businessDate: "2026-10-28",
};
