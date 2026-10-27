import assert from "node:assert/strict";
import { test } from "node:test";
import { APIError, api } from "../src/lib/client.ts";

test("typed requests encode IDs, include cookies and preserve explicit unimplemented errors", async (t) => {
  t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
    assert.ok(String(url).endsWith("/api/executions/case%2Fwith%20spaces"));
    assert.equal(init?.credentials, "include");
    assert.equal(init?.cache, "no-store");
    return new Response(JSON.stringify({ detail: "Case service not implemented" }), { status: 501 });
  });
  await assert.rejects(api.getExecution("case/with spaces"), (error: unknown) =>
    error instanceof APIError && error.status === 501 && error.message === "Case service not implemented");
});

test("decision payload contains only the allowed request fields and requires a CSRF token", async (t) => {
  const decision = { action: "APPROVE" as const, reason: "Reviewed current evidence.", gate_hash: "a".repeat(64), actor: "forged", payable_cents: 999999 };
  const mocked = t.mock.method(globalThis, "fetch", async (_url: string | URL | Request, init?: RequestInit) => {
    assert.equal(init?.method, "POST");
    assert.equal((init?.headers as Record<string, string>)["X-CSRF-Token"], "session-token");
    assert.deepEqual(JSON.parse(init?.body as string), { action: decision.action, reason: decision.reason, gate_hash: decision.gate_hash });
    return new Response(JSON.stringify({ detail: "stale" }), { status: 409 });
  });
  await assert.rejects(api.submitDecision("case", decision, ""), /CSRF/);
  assert.equal(mocked.mock.callCount(), 0);
  await assert.rejects(api.submitDecision("case", decision, "session-token"), /Case changed, review again/);
});
