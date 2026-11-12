import type { components } from "./api.ts";
import { usd } from "./case-display.ts";
import { APIError, api } from "./client.ts";

type Invoice = components["schemas"]["SupplierInvoice"];
export type NoticeAction = components["schemas"]["NoticeResponse"]["action"];
export const REASON_MAX = 1000;

const NOTICE_LABEL = { NOTICE_SENT: "Awaiting your response", ACCEPTED: "Accepted", CONTESTED: "Contested" } as const;

// R14: an allowlist, so any internal field the server might add never reaches the supplier screen.
export function supplierRow(invoice: Invoice) {
  const notice = invoice.notice;
  return {
    id: invoice.paypal_invoice_id,
    number: invoice.invoice_number ?? "Not numbered",
    vendor: invoice.vendor_name ?? "Supplier name unavailable",
    paypalStatus: invoice.paypal_status ?? "Status unavailable",
    billed: invoice.billed_cents === null ? "Unavailable" : usd(invoice.billed_cents),
    received: usd(invoice.received_cents),
    notice: notice && {
      id: notice.notice_id,
      text: notice.text, // untrusted model prose: render as a text node only
      shortfall: usd(notice.delta_cents),
      status: notice.status,
      statusLabel: NOTICE_LABEL[notice.status],
      sentAt: notice.created_at,
      respondedAt: notice.responded_at,
      canRespond: notice.status === "NOTICE_SENT",
    },
  };
}

export function respondBlock(action: NoticeAction, reason: string): string | null {
  if (reason.length > REASON_MAX) return `Keep the reason under ${REASON_MAX} characters.`;
  if (action === "CONTEST" && !reason.trim()) return "Enter a reason to contest the short payment.";
  return null;
}

export async function respondToNotice(
  noticeId: string, action: NoticeAction, reason: string, csrfToken: string, refresh: () => Promise<unknown>,
): Promise<string> {
  const blocked = respondBlock(action, reason);
  if (blocked) throw new Error(blocked);
  try {
    const trimmed = reason.trim();
    await api.respondNotice(noticeId, { action, ...(trimmed ? { reason: trimmed } : {}) }, csrfToken);
    return action === "ACCEPT" ? "Short payment accepted. Invoices reloaded." : "Short payment contested. Invoices reloaded.";
  } catch (error) {
    if (error instanceof APIError && error.status === 409) {
      throw new APIError(409, "This notice already has a response or is not open yet. Showing the current server state.");
    }
    throw error;
  } finally {
    // A lost response may still have been recorded; always re-read before showing actions again.
    await refresh();
  }
}
