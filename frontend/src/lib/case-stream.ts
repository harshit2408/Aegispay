import { APIError, api } from "./client.ts";
import type { caseReadState } from "./case-display.ts";
import { subscribeToExecution } from "./sse.ts";

export const RETRY_DELAYS_MS = [1000, 2000, 4000]; // bounded: after these, the user must press Retry

// Own the REST request and stream together so teardown also cancels pending reads.
export function watchCase(
  executionId: string,
  dispatch: (action: Parameters<typeof caseReadState>[1]) => void,
  status: (message: string, closed?: boolean) => void,
  retryDelays: readonly number[] = RETRY_DELAYS_MS,
  coalesceMs?: number,
): () => void {
  let active = true;
  let request: AbortController | undefined;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;

  // retry=true only from the backoff timer; any other trigger (first load, SSE event) restarts the budget.
  async function refresh(retry = false) {
    if (!active) return;
    if (!retry) failures = 0;
    clearTimeout(retryTimer);
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
        failures = 0;
        dispatch({ type: "loaded", data });
      }
    } catch (error) {
      if (!active || controller.signal.aborted) return;
      const terminal = wrongCase || error instanceof APIError &&
        (error.status === 501 || error.status < 500 && ![408, 429].includes(error.status));
      if (terminal) {
        active = false;
        unsubscribe();
      } else if (failures < retryDelays.length) {
        retryTimer = setTimeout(() => void refresh(true), retryDelays[failures++]);
      }
      const detail = error instanceof APIError && [401, 403].includes(error.status)
        ? "Your session cannot view this case. Sign in as a controller or CFO who did not upload the invoice, then retry."
        : error instanceof APIError && error.status === 404 ? "Case not found."
        : error instanceof Error ? error.message : "Case could not be loaded.";
      dispatch({ type: "failed", error: detail, terminal });
    }
  }
  // Open the stream up front so a failed first load still recovers on a later event or reconnect.
  const unsubscribe = subscribeToExecution(executionId, () => void refresh(), status, coalesceMs);
  void refresh();
  return () => { active = false; clearTimeout(retryTimer); request?.abort(); unsubscribe(); };
}
