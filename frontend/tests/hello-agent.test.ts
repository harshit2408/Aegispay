import assert from "node:assert/strict";
import { test } from "node:test";
import type { AgAiAgentRunContext, AgAiRunInput } from "ag-studio";
import { helloAgent } from "../src/app/studio/hello-agent.ts";

const input: AgAiRunInput = {
  threadId: "thread-probe", runId: "run-probe", messages: [], tools: [], state: {}, context: [], forwardedProps: {},
};
const context: AgAiAgentRunContext = { params: undefined, instructions: () => undefined, tools: () => [] };

test("scripted runner emits a complete hello-world run without tools or network", async () => {
  const events = [];
  for await (const event of helloAgent.run(input, context)) events.push(event);
  assert.deepEqual(events.map((event) => event.type), [
    "RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_FINISHED",
  ]);
  assert.deepEqual(events[0], { type: "RUN_STARTED", threadId: input.threadId, runId: input.runId });
  const content = events.find((event) => event.type === "TEXT_MESSAGE_CONTENT");
  assert.match(content?.delta ?? "", /Hello from the Studio Agent Framework/);
  assert.match(content?.delta ?? "", /scripted.*no LLM calls/);
  assert.equal(helloAgent.tools?.({} as never, undefined).length, 0);
});

test("an aborted probe emits no events", async () => {
  const events = [];
  for await (const event of helloAgent.run(input, { ...context, signal: AbortSignal.abort() })) events.push(event);
  assert.deepEqual(events, []);
});
