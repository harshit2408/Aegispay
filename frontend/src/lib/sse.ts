import type { components } from "./api.ts";

type StreamEvent = components["schemas"]["StreamEvent"];
const eventTypes = ["node", "settlement", "webhook"];

export function parseStreamEvent(raw: string): StreamEvent | null {
  try {
    if (raw.length > 2048) return null;
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const event = value as Record<string, unknown>;
    if (Object.keys(event).sort().join(",") !== "execution_id,name,status,ts,type") return null;
    if (typeof event.type !== "string" || !eventTypes.includes(event.type)) return null;
    if (typeof event.execution_id !== "string" || event.execution_id.length > 128 ||
      !/^[A-Za-z0-9_-]+$/.test(event.execution_id)) return null;
    for (const field of ["name", "status"]) {
      if (typeof event[field] !== "string" || event[field].length > 64 ||
        !/^[A-Za-z0-9_:-]+$/.test(event[field])) return null;
    }
    if (typeof event.ts !== "string" || event.ts.length > 64 ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(event.ts) ||
      !Number.isFinite(Date.parse(event.ts))) return null;
    return event as StreamEvent;
  } catch {
    return null;
  }
}

export function subscribeToExecution(
  executionId: string, refresh: () => void, status: (message: string) => void,
): () => void {
  const source = new EventSource(`${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/stream`, { withCredentials: true });
  let closed = false;
  function message(event: MessageEvent<string>) {
    if (closed) return;
    const data = parseStreamEvent(event.data);
    if (data?.execution_id === executionId && (event.type === "message" || event.type === data.type)) refresh();
  }
  function open() {
    if (closed) return;
    status("Connected to case updates.");
    refresh(); // Re-read current facts after initial connection and any missed reconnect events.
  }
  function error() {
    if (!closed) status(source.readyState === EventSource.CLOSED
      ? "Live updates unavailable. Reload to fetch the latest case."
      : "Updates disconnected. Reconnecting…");
  }
  for (const type of ["message", ...eventTypes]) source.addEventListener(type, message as EventListener);
  source.addEventListener("open", open);
  source.addEventListener("error", error);
  return () => {
    closed = true;
    for (const type of ["message", ...eventTypes]) source.removeEventListener(type, message as EventListener);
    source.removeEventListener("open", open);
    source.removeEventListener("error", error);
    source.close();
  };
}
