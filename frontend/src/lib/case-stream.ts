import { APIError, api } from "./client.ts";
import type { caseReadState } from "./case-display.ts";
import { subscribeToExecution } from "./sse.ts";

// Own the REST request and stream together so teardown also cancels pending reads.
export function watchCase(
  executionId: string,
  dispatch: (action: Parameters<typeof caseReadState>[1]) => void,
  status: (message: string) => void,
): () => void {
  let active = true;
  let request: AbortController | undefined;
  let unsubscribe: (() => void) | undefined;

  async function refresh() {
    if (!active) return;
    request?.abort();
    const controller = new AbortController();
    request = controller;
    dispatch({ type: "loading" });
    let wrongCase = false;
    try {
      const data = await api.getExecution(executionId, controller.signal);
      if (data.execution_id !== executionId) {
        wrongCase = true;
        throw new Error("The response belongs to another case.");
      }
      if (active && !controller.signal.aborted) {
        dispatch({ type: "loaded", data });
        if (!unsubscribe) unsubscribe = subscribeToExecution(executionId, refresh, status);
      }
    } catch (error) {
      if (!active || controller.signal.aborted) return;
      const terminal = wrongCase || error instanceof APIError &&
        (error.status === 501 || error.status < 500 && ![408, 429].includes(error.status));
      if (terminal) {
        active = false;
        unsubscribe?.();
      }
      const detail = error instanceof APIError && [401, 403].includes(error.status)
        ? "Sign in with an eligible operator session to view this case."
        : error instanceof APIError && error.status === 404 ? "Case not found."
        : error instanceof Error ? error.message : "Case could not be loaded.";
      dispatch({ type: "failed", error: detail });
    }
  }
  void refresh();
  return () => { active = false; request?.abort(); unsubscribe?.(); };
}
