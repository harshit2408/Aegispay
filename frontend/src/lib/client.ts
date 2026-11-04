import type { components, operations } from "./api.ts";

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
  const response = await fetch(path, {
    ...init, credentials: "include", cache: "no-store",
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `API request failed (${response.status})`;
    throw new APIError(response.status, detail);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

function post<T>(path: string, body: unknown, csrfToken: string): Promise<T> {
  if (!csrfToken.trim()) return Promise.reject(new Error("An operator session CSRF token is required"));
  return request(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify(body) });
}

export const api = {
  session: (): Promise<components["schemas"]["SessionView"]> => request("/api/auth/me"),
  login: (operatorId: string, password: string): Promise<components["schemas"]["SessionView"]> =>
    request("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operator_id: operatorId, password }) }),
  logout: (csrfToken: string): Promise<void> => post("/api/auth/logout", {}, csrfToken),
  activeMandate: (signal?: AbortSignal): Promise<components["schemas"]["ActiveMandateView"]> => request("/api/mandates/active", { signal }),
  listMandates: (signal?: AbortSignal): Promise<components["schemas"]["MandateRecord"][]> => request("/api/mandates", { signal }),
  compileMandate: (text: string, csrfToken: string): Promise<components["schemas"]["MandateRecord"]> => post("/api/mandates/compile", { text }, csrfToken),
  activateMandate: (policyId: string, version: number, reason: string, csrfToken: string): Promise<components["schemas"]["MandateRecord"]> =>
    post(`/api/mandates/${encodeURIComponent(policyId)}/versions/${version}/activate`, { reason }, csrfToken),
  listExecutions: (signal?: AbortSignal): Promise<Rows> => request("/api/executions", { signal }),
  uploadDocument: (data: FormData, csrfToken: string): Promise<components["schemas"]["DocumentReceipt"]> => {
    if (!csrfToken.trim()) return Promise.reject(new Error("An operator session CSRF token is required"));
    return request("/api/documents", { method: "POST", headers: { "X-CSRF-Token": csrfToken }, body: data });
  },
  getExecution: (id: string, signal?: AbortSignal): Promise<CaseDetail> => request(`/api/executions/${encodeURIComponent(id)}`, { signal }),
  // The case preview never calls this method. A real session must supply its CSRF token.
  submitDecision: (id: string, decision: Decision, csrfToken: string): Promise<Receipt> => {
    return post<Receipt>(`/api/executions/${encodeURIComponent(id)}/decisions`,
      { action: decision.action, reason: decision.reason, gate_hash: decision.gate_hash }, csrfToken)
      .catch((error: unknown) => {
        if (error instanceof APIError && error.status === 409) throw new APIError(409, "Case changed, review again.");
        throw error;
      });
  },
};
