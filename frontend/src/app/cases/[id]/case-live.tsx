"use client";

import { useEffect, useReducer, useState } from "react";
import { APIError, api } from "@/lib/client";
import { caseReadState } from "@/lib/case-display";
import { subscribeToExecution } from "@/lib/sse";
import { CaseDetails } from "./case-preview";

export function CaseLive({ executionId }: { executionId: string }) {
  const [state, dispatch] = useReducer(caseReadState, { loading: true, replayed: false });
  const [attempt, setAttempt] = useState(0);
  const [stream, setStream] = useState("Connecting to updates…");

  useEffect(() => {
    let active = true;
    let request: AbortController | undefined;
    let unsubscribe: (() => void) | undefined;

    async function refresh() {
      request?.abort();
      const controller = new AbortController();
      request = controller;
      dispatch({ type: "loading" });
      try {
        const data = await api.getExecution(executionId, controller.signal);
        if (data.execution_id !== executionId) throw new Error("The response belongs to another case.");
        if (active && !controller.signal.aborted) {
          dispatch({ type: "loaded", data });
          if (!unsubscribe) unsubscribe = subscribeToExecution(executionId, refresh, setStream);
        }
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        unsubscribe?.();
        unsubscribe = undefined;
        const detail = error instanceof APIError && [401, 403].includes(error.status)
          ? "Sign in with an eligible operator session to view this case."
          : error instanceof APIError && error.status === 404 ? "Case not found."
          : error instanceof Error ? error.message : "Case could not be loaded.";
        dispatch({ type: "failed", error: detail });
      }
    }
    void refresh();
    return () => { active = false; request?.abort(); unsubscribe?.(); };
  }, [executionId, attempt]);

  return <>
    {state.replayed && <div className="replay-banner">REPLAY · Recorded case data</div>}
    <p role="status">{state.loading ? "Loading current case…" : state.data ? stream : "Case unavailable"}</p>
    {state.error && <section className="empty-state" aria-labelledby="case-error-title">
      <h2 id="case-error-title">Case could not be loaded</h2>
      <p role="alert">{state.error}</p>
      <p className="locked-payee">Payee source: vendor master (locked)</p>
      <button type="button" onClick={() => setAttempt(attempt + 1)}>Retry</button>
    </section>}
    {state.data && <CaseDetails data={state.data} />}
  </>;
}
