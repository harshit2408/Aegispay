import assert from "node:assert/strict";
import test from "node:test";
import { APIError } from "../src/lib/client.ts";
import { demoFailure, showsDemoToolbar } from "../src/lib/demo-display.ts";

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
});

test("other demo conflicts keep the server detail; unknown failures stay generic", () => {
  assert.equal(demoFailure(new APIError(409, "Run S1 before the duplicate scenario")), "Run S1 before the duplicate scenario");
  assert.equal(demoFailure("boom"), "Demo action could not be confirmed.");
});
