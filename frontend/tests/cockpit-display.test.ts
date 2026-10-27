import assert from "node:assert/strict";
import { test } from "node:test";
import { previewCase } from "../src/fixtures/case-preview.ts";
import { frozenExposure, gaugeValue, triad } from "../src/lib/cockpit-display.ts";

test("triad distinguishes PO match, partial receipt and missing or mismatched source", () => {
  assert.deepEqual(triad(previewCase), { po: "Matched", receipt: "Partial", source: "Matched" });
  assert.deepEqual(triad({ ...previewCase, match: { ...previewCase.match, precision_ok: false,
    accepted_qty: 100, paypal_invoice_id: null } }), { po: "Review", receipt: "Accepted", source: "Missing" });
  assert.equal(triad({ ...previewCase, match: { ...previewCase.match, paypal_invoice_matches_pdf: false } }).source, "Review");
  assert.equal(triad({ ...previewCase, extraction: { ...previewCase.extraction, po_reference: "PO-OTHER" } }).po, "Review");
  assert.equal(triad({ ...previewCase, extraction: { ...previewCase.extraction, po_reference: null } }).po, "Review");
  assert.equal(triad({ ...previewCase, match: { ...previewCase.match, accepted_qty: 0 } }).receipt, "Not accepted");
  assert.equal(triad({ ...previewCase, match: { ...previewCase.match, grn_id: "" } }).receipt, "Missing");
  assert.equal(triad({ ...previewCase, match: { ...previewCase.match, accepted_qty: 0, billed_qty: 0 } }).receipt, "Review");
});

test("gauge handles zero caps, over-cap values and invalid cents without changing committed money", () => {
  assert.equal(gaugeValue(0, 0), null);
  assert.equal(gaugeValue(150, 0), null);
  assert.equal(gaugeValue(150, 100), 100);
  assert.equal(gaugeValue(90, 100), 90);
  for (const invalid of [-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => gaugeValue(invalid, 100));
    assert.throws(() => gaugeValue(0, invalid));
  }
});

test("frozen exposure deduplicates aliases per invoice and keeps overlapping reasons separate", () => {
  const data = { ...previewCase, gate: { ...previewCase.gate,
    reasons: ["HIGH_RISK", "G3 · Hidden text", "G3 · Payee mismatch", "OVER_DAILY_CAP", "HALTED"] } };
  assert.deepEqual(frozenExposure([data]), [
    { code: "G2", label: "Daily cap / halt", cents: 50000, count: 1 },
    { code: "G3", label: "High risk", cents: 50000, count: 1 },
  ]);
  assert.deepEqual(frozenExposure([]), []);
  assert.deepEqual(frozenExposure([{ ...data, status: "SETTLED" }]), []);
  assert.equal(frozenExposure([data, { ...data, execution_id: "second" }])[0].cents, 100000);
});

test("frozen exposure keeps unknown or missing reasons visible and rejects unsafe totals", () => {
  const data = { ...previewCase, gate: { ...previewCase.gate, reasons: [] } };
  assert.deepEqual(frozenExposure([data]), [{ code: "OTHER", label: "Other gate reasons", cents: 50000, count: 1 }]);
  assert.equal(frozenExposure([{ ...data, gate: { ...data.gate, reasons: ["G11 · PO exhausted"] } }])[0].code, "OTHER");
  assert.equal(frozenExposure([{ ...data, gate: { ...data.gate, reasons: ["constructor", "__proto__"] } }])[0].code, "OTHER");
  const huge = { ...data, match: { ...data.match, billed_cents: Number.MAX_SAFE_INTEGER } };
  assert.throws(() => frozenExposure([huge, huge]));
});
