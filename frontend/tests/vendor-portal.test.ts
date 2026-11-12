import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { APIError } from "../src/lib/client.ts";
import { respondBlock, respondToNotice, supplierRow } from "../src/lib/vendor-portal.ts";

const invoice = {
  paypal_invoice_id: "INV2-AAAA", invoice_number: "ACME-1042", vendor_name: "Acme Supplies", paypal_status: "PAID",
  billed_cents: 125000, received_cents: 100000,
  notice: { notice_id: "n-1", text: "<img src=x onerror=alert(1)> **Short** by $250.00", delta_cents: 25000, status: "NOTICE_SENT" as const, created_at: "2026-11-01T10:00:00Z", responded_at: null },
};

test("supplier row formats integer cents and exposes only supplier-facing fields (R14)", () => {
  const leaky = { ...invoice, risk_score: 0.9, gate_reasons: ["x"], threshold_cents: 1, mandate: { cap: 1 } };
  const row = supplierRow(leaky);
  assert.equal(row.billed, "$1,250.00");
  assert.equal(row.received, "$1,000.00");
  assert.equal(row.notice?.shortfall, "$250.00");
  assert.equal(row.notice?.canRespond, true);
  assert.equal(row.notice?.text, invoice.notice.text, "notice text is passed through verbatim for a text node");
  const json = JSON.stringify(row);
  for (const field of ["risk", "gate", "threshold", "mandate"]) assert.ok(!json.includes(field), field);
  assert.equal(supplierRow({ ...invoice, billed_cents: null, notice: null }).billed, "Unavailable");
  assert.equal(supplierRow({ ...invoice, notice: { ...invoice.notice, status: "ACCEPTED" } }).notice?.canRespond, false);
});

test("vendor page renders notice prose as plain text and shows no synthetic supplier data", () => {
  const source = readFileSync(new URL("../src/app/vendor/page.tsx", import.meta.url), "utf8");
  assert.ok(!source.includes("dangerouslySetInnerHTML"));
  assert.ok(!/markdown/i.test(source));
  assert.ok(!source.includes("previewCase"));
});

test("contest needs a reason; accept does not; reasons are capped", () => {
  assert.match(respondBlock("CONTEST", "  ") ?? "", /reason/);
  assert.equal(respondBlock("ACCEPT", ""), null);
  assert.equal(respondBlock("CONTEST", "Goods were delivered in full."), null);
  assert.match(respondBlock("ACCEPT", "x".repeat(1001)) ?? "", /1000/);
});

test("respond sends the CSRF header and only action + trimmed reason, then refreshes", async (t) => {
  let refreshed = 0;
  const fetchMock = t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
    assert.ok(String(url).endsWith("/api/vendor-sim/notices/n%2F1/respond"));
    assert.equal(init?.method, "POST");
    assert.equal(init?.credentials, "include");
    assert.equal((init?.headers as Record<string, string>)["X-CSRF-Token"], "csrf");
    assert.deepEqual(JSON.parse(init?.body as string), { action: "CONTEST", reason: "Delivered in full." });
    return Response.json({ notice_id: "n/1", short_pay_balance: "CONTESTED", responded_at: "2026-11-01T11:00:00Z" });
  });
  const message = await respondToNotice("n/1", "CONTEST", "  Delivered in full. ", "csrf", async () => { refreshed += 1; });
  assert.match(message, /contested/);
  assert.equal(refreshed, 1);
  await assert.rejects(respondToNotice("n/1", "CONTEST", "", "csrf", async () => {}), /reason/);
  await assert.rejects(respondToNotice("n/1", "ACCEPT", "", "", async () => {}), /CSRF/);
  assert.equal(fetchMock.mock.callCount(), 1);
});

test("409 (already answered or not sent) refreshes and reports server state", async (t) => {
  let refreshed = 0;
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "notice already answered" }, { status: 409 }));
  await assert.rejects(respondToNotice("n-1", "ACCEPT", "", "csrf", async () => { refreshed += 1; }),
    (error: unknown) => error instanceof APIError && error.status === 409 && /current server state/.test(error.message));
  assert.equal(refreshed, 1);
});
