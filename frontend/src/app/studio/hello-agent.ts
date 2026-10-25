import type { AgAiAgentRunner } from "ag-studio";

// shortcut: scripted framework probe only; replace for P3-11 after provider/auth/cost controls land.
export const helloAgent: AgAiAgentRunner = {
  id: "spike-hello",
  name: "Hello world (scripted)",
  description: "SPIKE-7 framework test. No LLM, tools or payment access.",
  tools: () => [],
  run: async function* ({ threadId, runId }, context) {
    if (context.signal?.aborted) return;
    const messageId = `hello-${runId}`;
    yield { type: "RUN_STARTED", threadId, runId };
    yield { type: "TEXT_MESSAGE_START", messageId, role: "assistant" };
    yield {
      type: "TEXT_MESSAGE_CONTENT",
      messageId,
      delta: "Hello from the Studio Agent Framework. This is a scripted SPIKE-7 probe with no LLM calls, tools or payment access.",
    };
    yield { type: "TEXT_MESSAGE_END", messageId };
    yield { type: "RUN_FINISHED", threadId, runId };
  },
};
