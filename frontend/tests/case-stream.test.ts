import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import { previewCase } from "../src/fixtures/case-preview.ts";
import { caseReadState, type CaseReadState } from "../src/lib/case-display.ts";
import { watchCase } from "../src/lib/case-stream.ts";

const executionId = "case_1";
const notification = { type: "node", execution_id: executionId, name: "match", status: "done", ts: "2026-10-29T08:00:00Z" };
const currentCase = () => Response.json({ ...previewCase, execution_id: executionId });
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

function watch(t: TestContext, replies: (Response | Error | Promise<Response>)[]) {
  const sources: FakeSource[] = [];
  class FakeSource extends EventTarget {
    static CLOSED = 2;
    readyState = 1;
    closed = false;
    constructor() { super(); sources.push(this); }
    close() { this.closed = true; }
    notify() { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(notification) })); }
  }
  const signals: AbortSignal[] = [];
  const originals = ["fetch", "EventSource"].map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)] as const);
  Object.defineProperty(globalThis, "EventSource", { value: FakeSource, configurable: true });
  Object.defineProperty(globalThis, "fetch", { configurable: true, value: async (_url: string, init: RequestInit) => {
    signals.push(init.signal as AbortSignal);
    const reply = replies.shift();
    assert.ok(reply, "Unexpected REST refresh");
    if (reply instanceof Error) throw reply;
    return reply;
  } });
  let state: CaseReadState = { loading: true, replayed: false };
  let updates = 0;
  const close = watchCase(executionId, (action) => { updates += 1; state = caseReadState(state, action); }, () => {});
  t.after(() => {
    close();
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else Reflect.deleteProperty(globalThis, name);
    }
  });
  return { sources, signals, close, state() { return state; }, get updates() { return updates; } };
}

for (const [name, failure] of [
  ["HTTP 503", () => Response.json({ detail: "Temporarily unavailable" }, { status: 503 })],
  ["network error", () => new TypeError("Failed to fetch")],
  ["timeout", () => new DOMException("Request timed out", "TimeoutError")],
  ["HTTP 408", () => Response.json({}, { status: 408 })],
  ["HTTP 429", () => Response.json({}, { status: 429 })],
] as const) {
  test(`${name} hides stale data but the same stream can recover on a later notification and reconnect`, async (t) => {
    const replies = [currentCase(), failure(), currentCase(), failure(), currentCase()];
    const run = watch(t, replies);
    await settle();
    assert.equal(run.state().data?.execution_id, executionId);
    const source = run.sources[0];
    source.notify();
    assert.equal(run.state().loading, true);
    assert.equal(run.state().data, undefined);
    await settle();
    assert.ok(run.state().error);
    assert.equal(run.state().data, undefined);
    assert.equal(run.state().replayed, true);
    assert.equal(source.closed, false);
    source.notify();
    await settle();
    assert.equal(run.state().data?.execution_id, executionId);
    assert.equal(run.state().error, undefined);
    assert.equal(run.sources.length, 1);
    source.notify();
    await settle();
    assert.ok(run.state().error);
    source.dispatchEvent(new Event("open"));
    await settle();
    assert.equal(run.state().data?.execution_id, executionId);
    assert.equal(run.sources.length, 1);
    assert.equal(run.signals.length, 5);
  });
}

for (const status of [401, 403, 404, 501]) {
  test(`HTTP ${status} clears the case and closes the stream without automatic recovery`, async (t) => {
    const run = watch(t, [currentCase(), Response.json({ detail: "Unavailable" }, { status })]);
    await settle();
    const source = run.sources[0];
    source.notify();
    await settle();
    assert.equal(source.closed, true);
    assert.equal(run.state().data, undefined);
    assert.equal(run.state().replayed, true);
    if ([401, 403].includes(status)) assert.match(run.state().error!, /Sign in/);
    const updates = run.updates;
    source.notify();
    source.dispatchEvent(new Event("open"));
    await settle();
    assert.equal(run.signals.length, 2);
    assert.equal(run.updates, updates);
  });
}

test("a mismatched case response closes the stream without displaying another case", async (t) => {
  const run = watch(t, [currentCase(), Response.json({ ...previewCase, execution_id: "other_case" })]);
  await settle();
  run.sources[0].notify();
  await settle();
  assert.equal(run.sources[0].closed, true);
  assert.equal(run.state().data, undefined);
  assert.match(run.state().error!, /another case/);
  assert.equal(run.state().replayed, true);
});

test("cleanup closes the stream, aborts pending REST and ignores late completions and events", async (t) => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  const run = watch(t, [currentCase(), pending]);
  await settle();
  const source = run.sources[0];
  source.notify();
  assert.equal(run.state().data, undefined);
  const updates = run.updates;
  run.close();
  assert.equal(source.closed, true);
  assert.equal(run.signals[1].aborted, true);
  finish(currentCase());
  source.notify();
  source.dispatchEvent(new Event("open"));
  await settle();
  assert.equal(run.updates, updates);
  assert.equal(run.signals.length, 2);
  assert.equal(run.state().data, undefined);
});
