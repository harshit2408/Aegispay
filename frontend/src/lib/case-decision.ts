import type { components } from "./api.ts";
import { decisionBlock } from "./case-display.ts";
import { api } from "./client.ts";

export async function decideCase(
  data: components["schemas"]["CaseDetail"],
  viewer: components["schemas"]["OperatorView"],
  action: components["schemas"]["DecisionRequest"]["action"],
  reason: string, csrfToken: string, refresh: () => void,
) {
  if (!data.gate) throw new Error("No gate decision is available.");
  const blocked = decisionBlock(data, viewer, data.approvals, data.gate.gate_hash, action, reason);
  if (blocked) throw new Error(blocked);
  try {
    return await api.submitDecision(data.execution_id, { action, reason: reason.trim(), gate_hash: data.gate.gate_hash }, csrfToken);
  } finally {
    // Even a lost response may follow a committed vote; re-read before another action.
    refresh();
  }
}
