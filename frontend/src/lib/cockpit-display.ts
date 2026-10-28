import type { components } from "./api.ts";
import { usd } from "./case-display.ts";

type CaseDetail = components["schemas"]["CaseDetail"];

export function triad(data: CaseDetail): { po: string; receipt: string; source: string } {
  return {
    po: data.extraction.po_reference === data.purchase_order.po_id && data.match.po_id === data.purchase_order.po_id &&
      data.match.precision_ok && data.match.billed_unit_price_cents === data.match.po_unit_price_cents &&
      data.match.billed_qty <= data.match.ordered_qty ? "Matched" : "Review",
    receipt: !data.match.grn_id || !data.goods_receipt.grn_id ? "Missing" :
      data.match.grn_id !== data.goods_receipt.grn_id || data.match.billed_qty <= 0 ? "Review" :
      data.match.accepted_qty <= 0 ? "Not accepted" : data.match.accepted_qty >= data.match.billed_qty ? "Accepted" : "Partial",
    source: !data.match.paypal_invoice_id ? "Missing" : data.match.paypal_invoice_matches_pdf ? "Matched" : "Review",
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
    if (data.status !== "FROZEN") continue;
    const codes = new Set(data.gate.reasons.map((reason) => {
      const explicit = reason.match(/^(G(?:[2-9]|10))\b/)?.[1];
      return explicit ?? (Object.hasOwn(reasonCodes, reason) ? reasonCodes[reason] : "OTHER");
    }));
    if (codes.size === 0) codes.add("OTHER");
    for (const code of codes) {
      const group = groups.get(code) ?? { code, label: reasonLabels[code] ?? "Other gate reasons", cents: 0, count: 0 };
      usd(data.match.billed_cents);
      group.cents += data.match.billed_cents;
      usd(group.cents);
      group.count += 1;
      groups.set(code, group);
    }
  }
  return [...groups.values()].sort((left, right) => right.cents - left.cents || left.code.localeCompare(right.code));
}
