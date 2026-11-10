import assert from "node:assert/strict";
import { test } from "node:test";
import { previewCase, mockReviewers } from "../src/fixtures/case-preview.ts";
import { decideCase } from "../src/lib/case-decision.ts";
import type { components } from "../src/lib/api.ts";
import { APIError, api } from "../src/lib/client.ts";

for (const status of [200, 409, 401, 503]) {
  test(`decision HTTP ${status} forces a fresh read and never changes displayed money locally`, async (t) => {
    const calls: string[] = [];
    const snapshot = JSON.stringify(previewCase);
    t.mock.method(globalThis, "fetch", async (url: string | URL | Request, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (init?.method === "POST") {
        assert.deepEqual(JSON.parse(init.body as string), {
          action: "APPROVE", reason: "Current invoice reviewed.", gate_hash: previewCase.gate.gate_hash,
        });
        assert.equal((init.headers as Record<string, string>)["X-CSRF-Token"], "csrf-real-session");
        return Response.json({ detail: "stale", approval_id: "server-id" }, { status });
      }
      return Response.json(previewCase);
    });
    let read: Promise<unknown> | undefined;
    const result = decideCase(previewCase, mockReviewers[0], "APPROVE", " Current invoice reviewed. ", "csrf-real-session", () => {
      read = api.getExecution(previewCase.execution_id);
    });
    if (status === 200) await result;
    else await assert.rejects(result, (error: unknown) => error instanceof APIError && error.status === status &&
      (status !== 409 || error.message === "Case changed, review again."));
    await read;
    assert.deepEqual(calls, [`POST /api/executions/${previewCase.execution_id}/decisions`, `GET /api/executions/${previewCase.execution_id}`]);
    assert.equal(JSON.stringify(previewCase), snapshot);
  });
}

test("a lost decision response still refreshes; invalid local decisions never send", async (t) => {
  let refreshes = 0;
  const mocked = t.mock.method(globalThis, "fetch", async () => { throw new TypeError("Network unavailable"); });
  const refresh = () => { refreshes += 1; };
  await assert.rejects(decideCase(previewCase, mockReviewers[0], "APPROVE", "Reviewed current invoice.", "token", refresh), /Network/);
  assert.equal(refreshes, 1);
  for (const [viewer, data, reason] of [
    [mockReviewers[2], previewCase, "Reviewed current invoice."],
    [mockReviewers[0], { ...previewCase, gate: { ...previewCase.gate, allowed_actions: [] } }, "Reviewed current invoice."],
    [mockReviewers[0], previewCase, " short "],
    [mockReviewers[0], { ...previewCase, gate: null }, "Reviewed current invoice."],
  ] satisfies [components["schemas"]["OperatorView"], components["schemas"]["CaseDetail"], string][]) await assert.rejects(decideCase(data, viewer, "APPROVE", reason, "token", refresh));
  assert.equal(mocked.mock.callCount(), 1);
  assert.equal(refreshes, 1);
});
