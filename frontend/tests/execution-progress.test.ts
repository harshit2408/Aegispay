import assert from "node:assert/strict";
import { test } from "node:test";
import { observedNodes, receiptLabel } from "../src/lib/execution-progress.ts";
import { demoMode } from "../src/lib/demo-display.ts";
import type { components } from "../src/lib/api.ts";

const event: components["schemas"]["StreamEvent"] = { type: "node", execution_id: "case_1", name: "match", status: "ANALYZING", ts: "2026-11-11T08:00:00Z" };

test("observed path contains only actual node notifications and bounds repeated reconnect history", () => {
  assert.deepEqual(observedNodes([], { ...event, type: "webhook" }), []);
  assert.deepEqual(observedNodes([event], { ...event, status: "DONE" }), [{ ...event, status: "DONE" }]);
  let events: typeof event[] = [];
  for (let i = 0; i < 40; i++) events = observedNodes(events, { ...event, name: `node_${i}` });
  assert.equal(events.length, 32);
  assert.equal(events[0].name, "node_8");
});

test("simulated receipt cannot display live verified proof", () => {
  const entry: components["schemas"]["SettlementEventView"] = { status: "SETTLED", simulated: true, verified: true, occurred_at: event.ts, paypal_id: "simulated_id" };
  assert.equal(receiptLabel(entry), "Simulated · no live settlement proof");
  assert.equal(receiptLabel({ ...entry, simulated: false }), "GET verified");
  assert.match(receiptLabel({ ...entry, simulated: false, verified: false }), /Unverified/);
});

test("demo flags require explicit boolean server values", () => {
  assert.deepEqual(demoMode({ demo_enabled: true, replay_mode: false }), { enabled: true, replayed: false });
  for (const value of [null, {}, { demo_enabled: "false", replay_mode: true }, { demo_enabled: true }]) {
    assert.throws(() => demoMode(value), /unavailable/);
  }
});
