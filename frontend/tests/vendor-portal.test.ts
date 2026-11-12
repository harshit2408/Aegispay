import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { APIError } from "../src/lib/client.ts";
import {
  displayTime, initialPortal, loadErrorMessage, portalReducer, recheckOnce, respondBlock, respondToNotice, supplierRow,
} from "../src/lib/vendor-portal.ts";

const invoice = {
  paypal_invoice_id: "INV2-AAAA", invoice_number: "ACME-1042", vendor_name: "Acme Supplies", paypal_status: "PAID",
  billed_cents: 125000, received_cents: 100000,
  notice: { notice_id: "n-1", text: "<img src=x onerror=alert(1)> **Short** by $250.00", delta_cents: 25000, status: "NOTICE_SENT" as const, created_at: "2026-11-01T10:00:00Z", responded_at: null },
};
const reloaded = async () => true;

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

test("one malformed amount degrades only its field, not the portal", () => {
  const state = portalReducer(portalReducer(initialPortal, { type: "load", generation: 1 }),
    { type: "loaded", generation: 1, invoices: [{ ...invoice, received_cents: -5 }, { ...invoice, paypal_invoice_id: "INV2-B", billed_cents: 1.5 }] });
  assert.equal(state.rows.length, 2);
  assert.equal(state.rows[0].received, "Amount unavailable");
  assert.equal(state.rows[0].billed, "$1,250.00");
  assert.equal(state.rows[1].billed, "Amount unavailable");
});

test("a superseded load result is ignored", () => {
  const first = portalReducer(initialPortal, { type: "load", generation: 1 });
  const second = portalReducer(first, { type: "load", generation: 2 });
  const fresh = portalReducer(second, { type: "loaded", generation: 2, invoices: [invoice] });
  assert.equal(portalReducer(fresh, { type: "loaded", generation: 1, invoices: [] }), fresh);
  assert.equal(portalReducer(fresh, { type: "failed", generation: 1, error: "old" }), fresh);
  assert.equal(fresh.rows.length, 1);
  assert.equal(fresh.loading, false);
});

test("load errors are friendly and a 401 re-checks the session only once", () => {
  assert.match(loadErrorMessage(new TypeError("fetch failed")), /Network problem/);
  assert.match(loadErrorMessage(new APIError(401, "x")), /Session expired/);
  assert.match(loadErrorMessage(new APIError(403, "x")), /role cannot/);
  const done = { current: false };
  assert.equal(recheckOnce(done), true);
  assert.equal(recheckOnce(done), false);
  assert.equal(recheckOnce(done), false);
});

test("dates render only when they parse", () => {
  assert.equal(displayTime("not a date"), null);
  assert.equal(typeof displayTime(invoice.notice.created_at), "string");
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

test("respond sends the CSRF header and only action + trimmed reason, then reports the reload", async (t) => {
  const fetchMock = t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
    assert.ok(String(url).endsWith("/api/vendor-sim/notices/n%2F1/respond"));
    assert.equal(init?.method, "POST");
    assert.equal(init?.credentials, "include");
    assert.equal((init?.headers as Record<string, string>)["X-CSRF-Token"], "csrf");
    assert.deepEqual(JSON.parse(init?.body as string), { action: "CONTEST", reason: "Delivered in full." });
    return Response.json({ notice_id: "n/1", short_pay_balance: "CONTESTED", responded_at: "2026-11-01T11:00:00Z" });
  });
  const lock = { current: false };
  const ok = await respondToNotice("n/1", "CONTEST", "  Delivered in full. ", "csrf", reloaded, lock);
  assert.deepEqual(ok, { ok: true, sessionExpired: false, message: "Short payment contested. The invoice now shows the saved response." });
  const stale = await respondToNotice("n/1", "CONTEST", "Delivered in full.", "csrf", async () => false, lock);
  assert.match(stale?.message ?? "", /could not be refreshed/);
  assert.match((await respondToNotice("n/1", "CONTEST", "", "csrf", reloaded, lock))?.message ?? "", /reason/);
  assert.equal((await respondToNotice("n/1", "ACCEPT", "", "", reloaded, lock))?.sessionExpired, true);
  assert.equal(fetchMock.mock.callCount(), 2);
  assert.equal(lock.current, false);
});

test("a second submit while one is in flight sends nothing", async (t) => {
  let release: () => void = () => {};
  const fetchMock = t.mock.method(globalThis, "fetch", () => new Promise<Response>((resolve) => {
    release = () => resolve(Response.json({ notice_id: "n-1", short_pay_balance: "ACCEPTED", responded_at: "2026-11-01T11:00:00Z" }));
  }));
  const lock = { current: false };
  const first = respondToNotice("n-1", "ACCEPT", "", "csrf", reloaded, lock);
  assert.equal(await respondToNotice("n-1", "ACCEPT", "", "csrf", reloaded, lock), null);
  release();
  assert.equal((await first)?.ok, true);
  assert.equal(fetchMock.mock.callCount(), 1);
});

test("409, 401, 403 and network failures each refresh and say what happened", async (t) => {
  const cases: [() => Promise<Response>, RegExp, boolean][] = [
    [async () => Response.json({ detail: "already answered" }, { status: 409 }), /Showing the current server state/, false],
    [async () => Response.json({ detail: "no session" }, { status: 401 }), /Session expired/, true],
    [async () => Response.json({ detail: "csrf" }, { status: 403 }), /^Your role cannot respond to supplier notices\.$/, false],
    [async () => { throw new TypeError("fetch failed"); }, /^Network problem\. Your response may not have been saved — refresh to check\.$/, false],
  ];
  for (const [respond, message, sessionExpired] of cases) {
    t.mock.method(globalThis, "fetch", respond);
    let reloads = 0;
    const outcome = await respondToNotice("n-1", "ACCEPT", "", "csrf", async () => { reloads += 1; return true; }, { current: false });
    assert.equal(outcome?.ok, false);
    assert.match(outcome?.message ?? "", message);
    assert.equal(outcome?.sessionExpired, sessionExpired);
    assert.equal(reloads, 1);
    t.mock.restoreAll();
  }
});
