"use client";

import { useEffect, useReducer, useState } from "react";
import { caseReadState } from "@/lib/case-display";
import { watchCase } from "@/lib/case-stream";
import { CaseDetails } from "./case-preview";

export function CaseLive({ executionId }: { executionId: string }) {
  const [state, dispatch] = useReducer(caseReadState, { loading: true, replayed: false });
  const [attempt, setAttempt] = useState(0);
  const [stream, setStream] = useState("Connecting to updates…");

  useEffect(() => watchCase(executionId, dispatch, setStream), [executionId, attempt]);

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
