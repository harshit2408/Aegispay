import type { components } from "./api.ts";
import { usd } from "./case-display.ts";
import { APIError, api } from "./client.ts";

type Invoice = components["schemas"]["SupplierInvoice"];
export type NoticeAction = components["schemas"]["NoticeResponse"]["action"];
export type Row = ReturnType<typeof supplierRow>;
export type Flag = { current: boolean };
export const REASON_MAX = 1000;

const NOTICE_LABEL = { NOTICE_SENT: "Awaiting your response", ACCEPTED: "Accepted", CONTESTED: "Contested" } as const;

// One malformed amount degrades its own field, never the whole portal.
function money(cents: number | null): string {
  if (cents === null) return "Unavailable";
  try { return usd(cents); } catch { return "Amount unavailable"; }
}

// R14: an allowlist, so any internal field the server might add never reaches the supplier screen.
export function supplierRow(invoice: Invoice) {
  const notice = invoice.notice;
  return {
    id: invoice.paypal_invoice_id,
    number: invoice.invoice_number ?? "Not numbered",
    vendor: invoice.vendor_name ?? "Supplier name unavailable",
    paypalStatus: invoice.paypal_status ?? "Status unavailable",
    billed: money(invoice.billed_cents),
    received: money(invoice.received_cents),
    notice: notice && {
      id: notice.notice_id,
      text: notice.text, // untrusted model prose: render as a text node only
      shortfall: money(notice.delta_cents),
      status: notice.status,
      statusLabel: NOTICE_LABEL[notice.status],
      sentAt: notice.created_at,
      respondedAt: notice.responded_at,
      canRespond: notice.status === "NOTICE_SENT",
    },
  };
}

/** Local display time for an ISO timestamp, or null when the server value does not parse. */
export function displayTime(iso: string): string | null {
  const time = new Date(iso);
  return Number.isNaN(time.getTime()) ? null : time.toLocaleString();
}

export type PortalState = { rows: Row[]; loading: boolean; error: string; generation: number };
export type PortalAction =
  | { type: "load"; generation: number }
  | { type: "loaded"; generation: number; invoices: Invoice[] }
  | { type: "failed"; generation: number; error: string };
export const initialPortal: PortalState = { rows: [], loading: true, error: "", generation: 0 };

// Only the newest load may write; a slower superseded response is dropped.
export function portalReducer(state: PortalState, action: PortalAction): PortalState {
  if (action.type === "load") return { ...state, loading: true, generation: action.generation };
  if (action.generation !== state.generation) return state;
  if (action.type === "loaded") return { ...state, rows: action.invoices.map(supplierRow), loading: false, error: "" };
  return { ...state, loading: false, error: action.error };
}

export function loadErrorMessage(error: unknown): string {
  if (!(error instanceof APIError)) return "Network problem. Supplier invoices could not be loaded. Check the connection and refresh.";
  if (error.status === 401) return "Session expired. Sign in again to open the supplier simulator.";
  if (error.status === 403) return "Your role cannot open the supplier simulator.";
  return "Supplier invoices could not be loaded. Try Refresh in a moment.";
}

/** True the first time only: a 401 re-checks the session once, never in a loop. */
export function recheckOnce(done: Flag): boolean {
  if (done.current) return false;
  done.current = true;
  return true;
}

export function respondBlock(action: NoticeAction, reason: string): string | null {
  if (reason.length > REASON_MAX) return `Keep the reason under ${REASON_MAX} characters.`;
  if (action === "CONTEST" && !reason.trim()) return "Enter a reason to contest the short payment.";
  return null;
}

export type RespondOutcome = { ok: boolean; message: string; sessionExpired: boolean };

function failureOutcome(error: unknown, reloaded: boolean): RespondOutcome {
  const fail = (message: string, sessionExpired = false) => ({ ok: false, message, sessionExpired });
  if (!(error instanceof APIError)) return fail("Network problem. Your response may not have been saved — refresh to check.");
  if (error.status === 401) return fail("Session expired. Sign in again to respond.", true);
  if (error.status === 403) return fail("Your role cannot respond to supplier notices.");
  if (error.status === 404) return fail("This notice no longer exists. Refresh to see current invoices.");
  if (error.status === 409) return fail(reloaded
    ? "This notice already has a response or is not open yet. Showing the current server state."
    : "This notice already has a response or is not open yet. Refresh to see the current server state.");
  return fail("The response could not be confirmed. Refresh to check whether it was saved.");
}

/**
 * Sends one supplier response and re-reads invoices. Returns null when another send is in flight
 * (`lock` is a ref shared by both buttons). `reload` resolves true when the list was refreshed.
 */
export async function respondToNotice(
  noticeId: string, action: NoticeAction, reason: string, csrfToken: string,
  reload: () => Promise<boolean>, lock: Flag,
): Promise<RespondOutcome | null> {
  const blocked = respondBlock(action, reason);
  if (blocked) return { ok: false, message: blocked, sessionExpired: false };
  if (!csrfToken.trim()) return { ok: false, message: "Session token missing. Sign in again to respond.", sessionExpired: true };
  if (lock.current) return null;
  lock.current = true;
  try {
    const trimmed = reason.trim();
    let failure: unknown = null;
    try {
      await api.respondNotice(noticeId, { action, ...(trimmed ? { reason: trimmed } : {}) }, csrfToken);
    } catch (error) { failure = error; }
    // A lost response may still have been recorded; always re-read before showing actions again.
    const reloaded = await reload();
    if (failure) return failureOutcome(failure, reloaded);
    const done = action === "ACCEPT" ? "Short payment accepted." : "Short payment contested.";
    return { ok: true, sessionExpired: false, message: reloaded ? `${done} The invoice now shows the saved response.` : `${done} The list could not be refreshed — use Refresh to see it.` };
  } finally { lock.current = false; }
}
