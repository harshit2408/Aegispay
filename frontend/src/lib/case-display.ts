import type { components } from "./api.ts";

type CaseDetail = components["schemas"]["CaseDetail"];
type Approval = components["schemas"]["ApprovalView"];
type Operator = components["schemas"]["OperatorView"];

export type CaseReadState = { data?: CaseDetail; error?: string; loading: boolean; replayed: boolean };

export function caseReadState(state: CaseReadState, action:
  { type: "loading" } | { type: "loaded"; data: CaseDetail } | { type: "failed"; error: string },
): CaseReadState {
  if (action.type === "loaded") return { data: action.data, loading: false, replayed: action.data.replayed };
  if (action.type === "failed") return { error: action.error, loading: false, replayed: state.replayed };
  return { loading: true, replayed: state.replayed };
}

export function usd(cents: number): string {
  if (!Number.isSafeInteger(cents) || cents < 0) throw new Error("Expected nonnegative safe integer cents");
  return `$${Math.trunc(cents / 100).toLocaleString("en-US")}.${String(cents % 100).padStart(2, "0")}`;
}

export function isEligible(viewer: Operator, uploadedBy: string): boolean {
  return viewer.operator_id !== uploadedBy && ["controller", "cfo"].includes(viewer.role);
}

export function currentApprovals(caseData: CaseDetail, approvals: Approval[]): Approval[] {
  const eligible = approvals.filter((approval) =>
    !approval.void_reason && approval.gate_hash === caseData.gate.gate_hash &&
    approval.action !== "REJECT" && isEligible(approval.actor, caseData.uploaded_by));
  return eligible.filter((approval, index) =>
    eligible.findIndex((other) => other.actor.operator_id === approval.actor.operator_id) === index);
}

export function decisionBlock(
  caseData: CaseDetail, viewer: Operator, approvals: Approval[], displayedHash: string,
  action: components["schemas"]["DecisionRequest"]["action"], reason: string,
): string | null {
  if (displayedHash !== caseData.gate.gate_hash) return "Case changed, review again.";
  if (!isEligible(viewer, caseData.uploaded_by)) return "Only a controller or CFO who did not upload this invoice can review.";
  if (!caseData.gate.allowed_actions.includes(action)) return "The gate does not allow this action.";
  if (action !== "REJECT" && caseData.gate.required_approvals === 99) return "This gate is not approvable.";
  if (approvals.some((approval) => !approval.void_reason && approval.gate_hash === displayedHash &&
    approval.actor.operator_id === viewer.operator_id)) return "This reviewer already recorded a decision for this gate.";
  if (reason.trim().length < 10) return "Enter a reason of at least 10 characters.";
  return null;
}
