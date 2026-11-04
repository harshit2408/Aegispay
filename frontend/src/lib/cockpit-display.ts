import type { components } from "./api.ts";
import { usd } from "./case-display.ts";

type CaseDetail = components["schemas"]["CaseDetail"];

export function triad(data: CaseDetail): { po: string; receipt: string; source: string } {
  const { extraction, purchase_order: po, goods_receipt: receipt, match } = data;
  if (!match) return { po: po ? "Pending" : "Missing", receipt: receipt ? "Pending" : "Missing", source: "Pending" };
  return {
    po: !po ? "Missing" : extraction?.po_reference === po.po_id && match.po_id === po.po_id &&
      match.precision_ok && match.billed_unit_price_cents === match.po_unit_price_cents &&
      match.billed_qty <= match.ordered_qty ? "Matched" : "Review",
    receipt: !match.grn_id || !receipt?.grn_id ? "Missing" :
      match.grn_id !== receipt.grn_id || match.billed_qty <= 0 ? "Review" :
      match.accepted_qty <= 0 ? "Not accepted" : match.accepted_qty >= match.billed_qty ? "Accepted" : "Partial",
    source: !match.paypal_invoice_id ? "Missing" : match.paypal_invoice_matches_pdf ? "Matched" : "Review",
  };
}

export function gaugeValue(committedCents: number, capCents: number): number | null {
  usd(committedCents);
  usd(capCents);
  return capCents === 0 ? null : Math.min(committedCents, capCents);
}

const reasonLabels: Record<string, string> = {
  G2: "Daily cap / halt", G3: "High risk", G4: "Single invoice limit", G5: "Price / quantity mismatch",
  G6: "Unverified / mismatched source", G7: "Possible duplicate", G8: "Automatic settlement",
  G9: "Short-pay", G10: "Needs review",
};
const reasonCodes: Record<string, string> = {
  OVER_DAILY_CAP: "G2", HALTED: "G2", HIGH_RISK: "G3", OVER_SINGLE_LIMIT: "G4", MISMATCH: "G5",
  UNVERIFIED_SOURCE: "G6", SOURCE_MISMATCH: "G6", POSSIBLE_DUPLICATE: "G7", NEEDS_REVIEW: "G10",
};

export function frozenExposure(cases: readonly CaseDetail[]) {
  const groups = new Map<string, { code: string; label: string; cents: number; count: number }>();
  for (const data of cases) {
    if (data.status !== "FROZEN" || !data.match) continue;
    const { match } = data;
    const codes = new Set((data.gate?.reasons ?? []).map((reason) => {
      const explicit = reason.match(/^(G(?:[2-9]|10))\b/)?.[1];
      return explicit ?? (Object.hasOwn(reasonCodes, reason) ? reasonCodes[reason] : "OTHER");
    }));
    if (codes.size === 0) codes.add("OTHER");
    for (const code of codes) {
      const group = groups.get(code) ?? { code, label: reasonLabels[code] ?? "Other gate reasons", cents: 0, count: 0 };
      usd(match.billed_cents);
      group.cents += match.billed_cents;
      usd(group.cents);
      group.count += 1;
      groups.set(code, group);
    }
  }
  return [...groups.values()].sort((left, right) => right.cents - left.cents || left.code.localeCompare(right.code));
}
