import type { operations } from "./api.ts";

type CaseDetail = operations["get_execution"]["responses"][200]["content"]["application/json"];
type Rows = operations["list_executions"]["responses"][200]["content"]["application/json"];
type Decision = operations["submit_decision"]["requestBody"]["content"]["application/json"];
type Receipt = operations["submit_decision"]["responses"][200]["content"]["application/json"];

export class APIError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "APIError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ""}${path}`, {
    ...init, credentials: "include", cache: "no-store",
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = response.status === 409 ? "Case changed, review again." :
      typeof body?.detail === "string" ? body.detail : `API request failed (${response.status})`;
    throw new APIError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listExecutions: (): Promise<Rows> => request("/api/executions"),
  getExecution: (id: string): Promise<CaseDetail> => request(`/api/executions/${encodeURIComponent(id)}`),
  // The case preview never calls this method. A real session must supply its CSRF token.
  submitDecision: (id: string, decision: Decision, csrfToken: string): Promise<Receipt> => {
    if (!csrfToken.trim()) return Promise.reject(new Error("An operator session CSRF token is required"));
    return request(`/api/executions/${encodeURIComponent(id)}/decisions`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ action: decision.action, reason: decision.reason, gate_hash: decision.gate_hash }),
    });
  },
};
