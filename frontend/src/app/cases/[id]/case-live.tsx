"use client";

import Link from "next/link";
import { useEffect, useReducer, useState } from "react";
import { caseReadState } from "@/lib/case-display";
import { watchCase } from "@/lib/case-stream";
import { CaseDetails } from "./case-preview";

export function CaseLive({ executionId }: { executionId: string }) {
  const [state, dispatch] = useReducer(caseReadState, { loading: true, replayed: false });
  const [attempt, setAttempt] = useState(0);
  const [stream, setStream] = useState({ message: "Connecting to updates…", closed: false });

  useEffect(
    () => watchCase(executionId, dispatch, (message, closed = false) => setStream({ message, closed })),
    [executionId, attempt]);

  const retry = <button type="button" onClick={() => {
    setStream({ message: "Connecting to updates…", closed: false });
    setAttempt(attempt + 1);
  }}>Retry</button>;
  return <>
    {state.replayed && <div className="replay-banner">REPLAY · Recorded case data</div>}
    {state.data && state.error && <div className="stale-banner" role="alert">
      Showing the last loaded case data, which may be out of date. {state.error}
    </div>}
    <p>{state.loading ? "Refreshing case…" : state.data ? stream.message : "Case unavailable"}</p>
    {stream.closed && !state.error && retry}
    {state.error && !state.data && <section className="empty-state" aria-labelledby="case-error-title">
      <h2 id="case-error-title">Case could not be loaded</h2>
      <p role="alert">{state.error}</p>
      <p className="locked-payee">Payee source: vendor master (locked)</p>
      {retry} <Link href="/">Back to cockpit</Link>
    </section>}
    {state.data && state.error && retry}
    {state.data && <CaseDetails data={state.data} />}
  </>;
}
