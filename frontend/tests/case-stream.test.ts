import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import { previewCase } from "../src/fixtures/case-preview.ts";
import { caseReadState, type CaseReadState } from "../src/lib/case-display.ts";
import { watchCase } from "../src/lib/case-stream.ts";

const executionId = "case_1";
const notification = { type: "node", execution_id: executionId, name: "match", status: "done", ts: "2026-10-29T08:00:00Z" };
const currentCase = () => Response.json({ ...previewCase, execution_id: executionId });
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 20));

function watch(t: TestContext, replies: (Response | Error | Promise<Response>)[], delays: number[] = []) {
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
  const close = watchCase(executionId, (action) => { updates += 1; state = caseReadState(state, action); }, () => {}, delays, 0);
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
  test(`${name} keeps stale data and the same stream can recover on a later notification and reconnect`, async (t) => {
    const replies = [currentCase(), failure(), currentCase(), failure(), currentCase()];
    const run = watch(t, replies);
    await settle();
    assert.equal(run.state().data?.execution_id, executionId);
    const source = run.sources[0];
    source.notify();
    await settle();
    assert.ok(run.state().error);
    assert.equal(run.state().data?.execution_id, executionId);
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
    source.dispatchEvent(new Event("open")); // initial open: no extra fetch
    await settle();
    assert.equal(run.signals.length, 5 - 1);
    source.dispatchEvent(new Event("open")); // reconnect refetches
    await settle();
    assert.equal(run.state().error, undefined);
    assert.equal(run.sources.length, 1);
    assert.equal(run.signals.length, 5);
  });
}

for (const status of [401, 403, 404, 501]) {
  test(`HTTP ${status} clears the case and closes the stream without automatic recovery`, async (t) => {
    const run = watch(t, [currentCase(), Response.json({ detail: "Unavailable" }, { status })]);
    await settle();
    const source = run.sources[0];
    await settle();
    source.notify();
    await settle();
    assert.equal(source.closed, true);
    assert.equal(run.state().data, undefined);
    assert.equal(run.state().replayed, true);
    if ([401, 403].includes(status)) assert.match(run.state().error!, /Sign in.*retry/);
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
  await settle();
  assert.equal(run.signals.length, 2);
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
});

test("a transient first-load failure still opens the stream and retries with backoff, then stops when bounded", async (t) => {
  const run = watch(t, [new TypeError("Failed to fetch"), Response.json({}, { status: 503 }), currentCase()], [1, 1]);
  assert.equal(run.sources.length, 1);
  await settle();
  assert.equal(run.state().data?.execution_id, executionId);
  assert.equal(run.state().error, undefined);
  assert.equal(run.signals.length, 3);
  const bounded = watch(t, [new TypeError("a"), new TypeError("b"), new TypeError("c")], [1, 1]);
  await settle();
  assert.equal(bounded.signals.length, 3);
  assert.ok(bounded.state().error);
});

test("first connection loads once; a failed refresh keeps the previous case visible", async (t) => {
  const run = watch(t, [currentCase(), Response.json({}, { status: 503 })]);
  run.sources[0].dispatchEvent(new Event("open"));
  await settle();
  assert.equal(run.signals.length, 1);
  run.sources[0].notify();
  await settle();
  assert.equal(run.state().data?.execution_id, executionId);
  assert.ok(run.state().error);
});

test("event bursts coalesce into one refresh", async (t) => {
  const run = watch(t, [currentCase(), currentCase()]);
  await settle();
  for (let i = 0; i < 5; i++) run.sources[0].notify();
  await settle();
  assert.equal(run.signals.length, 2);
});
