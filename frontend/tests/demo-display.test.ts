import assert from "node:assert/strict";
import test from "node:test";
import { APIError } from "../src/lib/client.ts";
import { controlSummary, createDemoRunner, demoFailure, detectorPressed, showsDemoToolbar, type DemoRunState } from "../src/lib/demo-display.ts";

test("demo toolbar renders for admin only and only when demo is enabled", () => {
  assert.equal(showsDemoToolbar({ enabled: true }, "admin"), true);
  for (const role of ["controller", "cfo", "receiver", "vendor_admin", undefined]) {
    assert.equal(showsDemoToolbar({ enabled: true }, role), false);
  }
  assert.equal(showsDemoToolbar({ enabled: false }, "admin"), false);
  assert.equal(showsDemoToolbar(undefined, "admin"), false);
});

test("live reset outcomes map to explicit pending, failure, throttle and role messages", () => {
  assert.match(demoFailure(new APIError(504, "Reset pending")), /still pending.*again/);
  assert.match(demoFailure(new APIError(502, "Reset failed")), /failed in the owner reset runner/);
  assert.match(demoFailure(new APIError(429, "throttled")), /Too many/);
  assert.match(demoFailure(new APIError(403, "Forbidden")), /admin/);
  assert.equal(demoFailure(new APIError(503, "Decision runtime unavailable")), "Unavailable on this server: Decision runtime unavailable.");
  // No double period when the server detail already ends with one.
  assert.equal(demoFailure(new APIError(503, "Live document admission is unavailable.")), "Unavailable on this server: Live document admission is unavailable.");
});

test("401 and 403 are distinct; 403 shows the server reason (role vs CSRF)", () => {
  assert.match(demoFailure(new APIError(401, "session expired")), /^Session expired/);
  assert.match(demoFailure(new APIError(403, "CSRF token required")), /CSRF token required/);
  assert.match(demoFailure(new APIError(403, "role not permitted")), /role not permitted/);
});

test("network failure reports an unknown outcome instead of inviting a blind retry", () => {
  assert.match(demoFailure(new TypeError("Failed to fetch")), /outcome unknown.*run id/);
});

test("other demo conflicts keep the server detail; unknown failures stay generic", () => {
  assert.equal(demoFailure(new APIError(409, "Run S1 before the duplicate scenario")), "Run S1 before the duplicate scenario");
  assert.equal(demoFailure("boom"), "Demo action could not be confirmed.");
});

test("a 504 reset is attempted once, reported, and never retried automatically", async () => {
  const runner = createDemoRunner();
  const states: DemoRunState[] = [];
  let calls = 0;
  await runner.run("Resetting…", async () => { calls += 1; throw new APIError(504, "Reset pending"); }, (state) => states.push(state));
  assert.equal(calls, 1);
  assert.deepEqual(states.map((state) => state.pending), ["Resetting…", ""]);
  assert.match(states.at(-1)!.failure, /still pending/);
  assert.equal(runner.pending, "");
});

test("controls stay busy while a request is in flight and a second click is ignored", async () => {
  const runner = createDemoRunner();
  let release!: (value: string) => void;
  const states: DemoRunState[] = [];
  const first = runner.run("Running S1…", () => new Promise<string>((resolve) => { release = resolve; }), (state) => states.push(state));
  assert.equal(runner.pending, "Running S1…"); // the toolbar disables every button while this is set
  let second = 0;
  await runner.run("Running S2…", async () => { second += 1; return "x"; }, (state) => states.push(state));
  assert.equal(second, 0);
  release("S1 admitted.");
  await first;
  assert.equal(runner.pending, "");
  assert.deepEqual(states.at(-1), { pending: "", message: "S1 admitted.", failure: "" });
});

test("detector aria-pressed follows the server state and toggles with it", () => {
  assert.deepEqual([detectorPressed(undefined, true), detectorPressed(undefined, false)], [false, false]);
  const on = { run_id: "r1", detector_enabled: true };
  assert.deepEqual([detectorPressed(on, true), detectorPressed(on, false)], [true, false]);
  const off = { ...on, detector_enabled: false };
  assert.deepEqual([detectorPressed(off, true), detectorPressed(off, false)], [false, true]);
});

test("control summary is labelled last confirmed while stale", () => {
  const view = { run_id: "r1", detector_enabled: false };
  assert.equal(controlSummary(view, false), "Run r1 · Detector OFF (server confirmed)");
  assert.equal(controlSummary(view, true), "Run r1 · Detector OFF (last confirmed)");
});
