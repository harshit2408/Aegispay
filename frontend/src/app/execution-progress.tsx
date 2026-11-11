"use client";

import { useEffect, useReducer, useState } from "react";
import type { components } from "@/lib/api";
import { caseReadState } from "@/lib/case-display";
import { watchCase } from "@/lib/case-stream";
import { observedNodes, receiptLabel } from "@/lib/execution-progress";
import { useSession } from "./session";

type Event = components["schemas"]["StreamEvent"];

export function DecisionPath({ events }: { events: readonly Event[] }) {
  return <><h3>Decision path</h3>
    <p className="muted">Observed node notifications for this connection. Earlier steps are not reconstructed.</p>
    {events.length ? <ol className="decision-path">{events.map((event) => <li key={event.name}>
      <strong>{event.name.replaceAll("_", " ")}</strong> · {event.status} <time dateTime={event.ts}>{event.ts}</time>
    </li>)}</ol> : <p>No node notifications observed yet. Current case facts appear below.</p>}
  </>;
}

export function SettlementTimeline({ entries }: { entries: components["schemas"]["SettlementEventView"][] }) {
  return <><h3>Settlement timeline</h3>
    {entries.length === 0 ? <p>No settlement events recorded.</p> : <ol className="settlement-timeline">{entries.map((entry, index) =>
      <li key={`${entry.status}:${index}`}><strong>{entry.status.replaceAll("_", " ")}</strong> · {entry.occurred_at ?? "Not reached"}<br />
        <span className={`case-badge ${entry.simulated || !entry.verified ? "warning" : "success"}`}>{receiptLabel(entry)}</span>
        <p>PayPal reference: <code>{entry.paypal_id ?? "Not available"}</code></p>
      </li>)}</ol>}
  </>;
}

export function LiveExecutionProgress({ executionId, timeline = false }: { executionId: string; timeline?: boolean }) {
  const { operator, busy } = useSession();
  if (!operator || busy) return <p>Sign in to follow an execution.</p>;
  if (!executionId) return <p>Select an execution above to follow {timeline ? "settlement" : "its decision path"}.</p>;
  return <Progress key={`${operator.operator_id}:${operator.csrf_token}:${executionId}`} executionId={executionId} timeline={timeline} />;
}

function Progress({ executionId, timeline }: { executionId: string; timeline: boolean }) {
  const [state, dispatch] = useReducer(caseReadState, { loading: true, replayed: false });
  const [events, setEvents] = useState<Event[]>([]);
  const [status, setStatus] = useState("Connecting…");
  useEffect(() => watchCase(executionId, dispatch, setStatus, undefined, undefined,
    (event) => setEvents((previous) => observedNodes(previous, event))), [executionId]);
  return <section className="cockpit-widget" aria-label={timeline ? "Settlement timeline" : "Decision path"}>
    {state.replayed && <div className="replay-banner">REPLAY · Recorded case data</div>}
    <p role="status">{state.error ?? status}</p>
    {state.data && !state.error && <p>Current status: <strong>{state.data.status}</strong></p>}
    {timeline ? <SettlementTimeline entries={state.error ? [] : state.data?.settlement_timeline ?? []} /> : <DecisionPath events={state.error ? [] : events} />}
  </section>;
}
