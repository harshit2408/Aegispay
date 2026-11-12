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
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ""}${path}`, {
    ...init, credentials: "include", cache: "no-store",
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    // Demo 409s are control-state conflicts ("Run S1 before the duplicate scenario"), not stale cases.
    const detail = response.status === 409 && !path.startsWith("/api/demo/") ? "Case changed, review again." :
      typeof body?.detail === "string" ? body.detail : `API request failed (${response.status})`;
    throw new APIError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  // A 2xx with an unparsable body is a transient server fault, not a raw SyntaxError for the user.
  return response.json().catch((error: unknown) => {
    if (error instanceof DOMException && error.name === "AbortError") throw error; // caller cancelled
    throw new APIError(502, "The server sent an unreadable response.");
  }) as Promise<T>;
}

function post<T>(path: string, body: unknown, csrfToken: string, signal?: AbortSignal): Promise<T> {
  if (!csrfToken.trim()) return Promise.reject(new Error("An operator session CSRF token is required"));
  return request(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify(body), signal });
}

export const api = {
  analystWidget: (question: string, csrfToken: string, signal?: AbortSignal): Promise<components["schemas"]["AnalystWidget"]> => post("/api/analyst/widgets", { question }, csrfToken, signal),
  opsStatus: (signal?: AbortSignal): Promise<components["schemas"]["OpsStatusView"]> => request("/api/ops/status", { signal }),
  setHalt: (halted: boolean, reason: string, csrfToken: string): Promise<components["schemas"]["HaltView"]> => post("/api/ops/settlement-halt", { halted, reason }, csrfToken),
  health: (signal?: AbortSignal): Promise<unknown> => request("/api/health", { signal }),
  resetDemo: (csrfToken: string): Promise<components["schemas"]["DemoControlView"]> => post("/api/demo/reset", {}, csrfToken),
  detector: (signal?: AbortSignal): Promise<components["schemas"]["DemoControlView"]> => request("/api/demo/forensics-detector", { signal }),
  setDetector: (enabled: boolean, csrfToken: string): Promise<components["schemas"]["DemoControlView"]> => post("/api/demo/forensics-detector", { enabled }, csrfToken),
  runScenario: (scenario: "S1" | "S2" | "S3" | "S4", csrfToken: string): Promise<components["schemas"]["DocumentReceipt"]> =>
    post(`/api/demo/scenarios/${scenario}/run`, {}, csrfToken),
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
