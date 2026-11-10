import type { components } from "./api.ts";

type StreamEvent = components["schemas"]["StreamEvent"];
const eventTypes = ["node", "settlement", "webhook"] as const satisfies readonly StreamEvent["type"][];
const REFRESH_COALESCE_MS = 150;
const isEventType = (value: unknown): value is StreamEvent["type"] =>
  eventTypes.some((type) => type === value);

export function parseStreamEvent(raw: string): StreamEvent | null {
  try {
    if (raw.length > 2048) return null;
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const event = value as Record<string, unknown>;
    if (Object.keys(event).sort().join(",") !== "execution_id,name,status,ts,type") return null;
    if (!isEventType(event.type)) return null;
    const type = event.type;
    if (typeof event.execution_id !== "string" || event.execution_id.length > 128 ||
      !/^[A-Za-z0-9_-]+$/.test(event.execution_id)) return null;
    const { name, status } = event;
    const isToken = (field: unknown): field is string =>
      typeof field === "string" && field.length <= 64 && /^[A-Za-z0-9_:-]+$/.test(field);
    if (!isToken(name) || !isToken(status)) return null;
    if (typeof event.ts !== "string" || event.ts.length > 64 ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(event.ts) ||
      !Number.isFinite(Date.parse(event.ts))) return null;
    return { type, execution_id: event.execution_id, name, status, ts: event.ts };
  } catch {
    return null;
  }
}

export function subscribeToExecution(
  executionId: string, refresh: () => void, status: (message: string, closed?: boolean) => void,
  coalesceMs = REFRESH_COALESCE_MS,
): () => void {
  const source = new EventSource(`${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/stream`, { withCredentials: true });
  let closed = false;
  let opened = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Fire once per burst; a trailing timer that is not reset cannot be starved by further events.
  function schedule() {
    timer ??= setTimeout(() => { timer = undefined; if (!closed) refresh(); }, coalesceMs);
  }
  function message(event: MessageEvent<string>) {
    if (closed) return;
    const data = parseStreamEvent(event.data);
    if (data?.execution_id === executionId && (event.type === "message" || event.type === data.type)) schedule();
  }
  function open() {
    if (closed) return;
    status("Connected to case updates.");
    // The first open follows the caller's initial load; only reconnects can have missed events.
    if (opened) schedule();
    opened = true;
  }
  function error() {
    if (closed) return;
    if (source.readyState === EventSource.CLOSED) status("Live updates unavailable. Reload to fetch the latest case.", true);
    else status("Updates disconnected. Reconnecting…");
  }
  for (const type of ["message", ...eventTypes]) source.addEventListener(type, message as EventListener);
  source.addEventListener("open", open);
  source.addEventListener("error", error);
  return () => {
    closed = true;
    clearTimeout(timer);
    for (const type of ["message", ...eventTypes]) source.removeEventListener(type, message as EventListener);
    source.removeEventListener("open", open);
    source.removeEventListener("error", error);
    source.close();
  };
}
