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

test("case refresh forwards cancellation to fetch instead of retaining a superseded request", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (_url: string | URL | Request, init?: RequestInit) => {
    assert.equal(init?.signal, controller.signal);
    throw new DOMException("Cancelled", "AbortError");
  });
  controller.abort();
  await assert.rejects(api.getExecution("case", controller.signal), { name: "AbortError" });
});

test("real session lifecycle uses same-origin cookies, private CSRF header and no browser-stored credentials", async (t) => {
  const session = { operator_id: "controller-2", role: "controller", csrf_token: "private-csrf" };
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
    calls.push(String(url));
    assert.equal(init?.credentials, "include");
    assert.equal(init?.cache, "no-store");
    if (url === "/api/auth/login") {
      assert.deepEqual(JSON.parse(init?.body as string), { operator_id: "controller-2", password: "test-password" });
      assert.equal((init?.headers as Record<string, string>)["X-CSRF-Token"], undefined);
    }
    if (url === "/api/auth/logout") {
      assert.equal((init?.headers as Record<string, string>)["X-CSRF-Token"], session.csrf_token);
      assert.equal(init?.method, "POST");
      return new Response(null, { status: 204 });
    }
    return Response.json(session);
  });
  assert.deepEqual(await api.login("controller-2", "test-password"), session);
  assert.deepEqual(await api.session(), session);
  await assert.rejects(api.logout(""), /CSRF/);
  await api.logout(session.csrf_token);
  assert.deepEqual(calls, ["/api/auth/login", "/api/auth/me", "/api/auth/logout"]);
});

test("mandate compile and activation send only server contract inputs with CSRF", async (t) => {
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
    calls.push(String(url));
    assert.equal(init?.credentials, "include");
    if (init?.method === "POST") {
      assert.equal((init.headers as Record<string, string>)["X-CSRF-Token"], "token");
      assert.deepEqual(JSON.parse(init.body as string), String(url).endsWith("compile")
        ? { text: "Daily cap $5000." } : { reason: "Reviewed draft and clamps." });
    }
    return Response.json(String(url) === "/api/mandates" ? [] : {});
  });
  await api.listMandates(); await api.activeMandate();
  await api.compileMandate("Daily cap $5000.", "token");
  await api.activateMandate("policy/one", 2, "Reviewed draft and clamps.", "token");
  assert.deepEqual(calls, ["/api/mandates", "/api/mandates/active", "/api/mandates/compile", "/api/mandates/policy%2Fone/versions/2/activate"]);
});

test("document upload sends only multipart evidence fields with session CSRF", async (t) => {
  const data = new FormData();
  data.set("file", new File(["%PDF-1.7"], "invoice.pdf", { type: "application/pdf" }));
  data.set("po_id", "PO-1043");
  data.set("paypal_invoice_id", "INV2-sample");
  const mock = t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
    assert.equal(url, "/api/documents");
    assert.equal(init?.method, "POST");
    assert.equal(init.credentials, "include");
    assert.deepEqual(init.headers, { Accept: "application/json", "X-CSRF-Token": "csrf" });
    assert.equal(init.body, data);
    return Response.json({ execution_id: "new-case", duplicate_of_execution_id: "first-case" });
  });
  await assert.rejects(api.uploadDocument(data, " "), /CSRF/);
  assert.equal(mock.mock.callCount(), 0);
  assert.deepEqual(await api.uploadDocument(data, "csrf"), {
    execution_id: "new-case", duplicate_of_execution_id: "first-case",
  });
});
