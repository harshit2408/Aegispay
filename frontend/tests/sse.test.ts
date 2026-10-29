import assert from "node:assert/strict";
import { test } from "node:test";
import { parseStreamEvent, subscribeToExecution } from "../src/lib/sse.ts";

const event = { type: "settlement", execution_id: "case_1", name: "payout", status: "PAYOUT_SUCCEEDED", ts: "2026-10-28T08:00:00Z" };

test("stream accepts only the minimal bounded contract and timezone-aware timestamps", () => {
  assert.deepEqual(parseStreamEvent(JSON.stringify(event)), event);
  assert.ok(parseStreamEvent(JSON.stringify({ ...event, ts: "2026-10-28T08:00:00+05:30" })));
  for (const invalid of [null, [], {}, { ...event, type: "payment" }, { ...event, payload: "invoice text" },
    { ...event, execution_id: "case/1" }, { ...event, execution_id: "x".repeat(129) },
    { ...event, name: "x".repeat(65) }, { ...event, status: "" }, { ...event, status: "invoice text" },
    { ...event, ts: "2026-10-28" }, { ...event, ts: "2026-10-28T08:00:00" }, { ...event, ts: "invalid" }]) {
    assert.equal(parseStreamEvent(JSON.stringify(invalid)), null);
  }
  assert.equal(parseStreamEvent("not JSON"), null);
  assert.equal(parseStreamEvent(" ".repeat(2049) + JSON.stringify(event)), null);
});

test("credentialed stream refreshes only its case, recovers on reconnect and closes on cleanup", (t) => {
  class FakeSource extends EventTarget {
    static CLOSED = 2;
    static instance: FakeSource;
    readyState = 1;
    closed = false;
    url: string;
    options?: EventSourceInit;
    constructor(url: string, options?: EventSourceInit) {
      super(); this.url = url; this.options = options; FakeSource.instance = this;
    }
    close() { this.closed = true; }
    send(type: string, data: unknown) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) })); }
  }
  const original = Object.getOwnPropertyDescriptor(globalThis, "EventSource");
  Object.defineProperty(globalThis, "EventSource", { value: FakeSource, configurable: true });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, "EventSource", original);
    else Reflect.deleteProperty(globalThis, "EventSource");
  });
  let refreshes = 0;
  const statuses: string[] = [];
  const close = subscribeToExecution("case_1", () => { refreshes += 1; }, (status) => statuses.push(status));
  const source = FakeSource.instance;
  assert.ok(source.url.endsWith("/api/stream"));
  assert.equal(source.options?.withCredentials, true);
  source.send("message", event);
  source.send("settlement", event);
  assert.equal(refreshes, 2);
  source.send("node", event);
  source.send("message", { ...event, execution_id: "other" });
  source.send("message", { ...event, payload: { payee: "forged" } });
  assert.equal(refreshes, 2);
  source.dispatchEvent(new Event("open"));
  source.dispatchEvent(new Event("open"));
  assert.equal(refreshes, 4);
  source.dispatchEvent(new Event("error"));
  assert.match(statuses.at(-1)!, /Reconnecting/);
  source.readyState = FakeSource.CLOSED;
  source.dispatchEvent(new Event("error"));
  assert.match(statuses.at(-1)!, /unavailable/);
  const previousStatuses = statuses.length;
  close();
  assert.equal(source.closed, true);
  source.send("message", event);
  source.dispatchEvent(new Event("open"));
  source.dispatchEvent(new Event("error"));
  assert.equal(refreshes, 4);
  assert.equal(statuses.length, previousStatuses);
});
