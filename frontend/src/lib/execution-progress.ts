import type { components } from "./api.ts";

type Event = components["schemas"]["StreamEvent"];

export function observedNodes(events: readonly Event[], event: Event): Event[] {
  if (event.type !== "node") return [...events];
  // Keep the latest status for each observed node; reconnects never imply missing steps succeeded.
  return [...events.filter((previous) => previous.name !== event.name), event].slice(-32);
}

export function receiptLabel(entry: components["schemas"]["SettlementEventView"]): string {
  if (entry.simulated) return "Simulated · no live settlement proof";
  return entry.verified ? "GET verified" : "Unverified · awaiting confirmation";
}
