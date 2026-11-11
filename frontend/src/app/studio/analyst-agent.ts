import type { AgAiAgentRunner } from "ag-studio";
import type { components } from "../../lib/api.ts";

type Widget = components["schemas"]["AnalystWidget"];

export function createAnalystAgent(
  request: (question: string, signal?: AbortSignal) => Promise<Widget>,
  showWidget: (widget: Widget | undefined) => void,
  showError: (message: string) => void,
): AgAiAgentRunner {
  return {
    id: "treasury-analyst",
    name: "Treasury Analyst",
    description: "Build a read-only execution list or status summary from an authenticated server snapshot.",
    tools: () => [],
    run: async function* (input, context) {
      if (context.signal?.aborted) return;
      yield { type: "RUN_STARTED", threadId: input.threadId, runId: input.runId };
      showWidget(undefined);
      showError("");
      try {
        const question = input.messages.findLast((message) => message.role === "user")?.content?.trim();
        if (!question || question.length > 1000) throw new Error("Enter a question of 1–1000 characters.");
        const widget = await request(question, context.signal);
        if (context.signal?.aborted) return;
        if (!["status_summary", "execution_list"].includes(widget.kind)) throw new Error("Unsupported analyst widget.");
        showWidget(widget);
        const messageId = `analyst-${input.runId}`;
        yield { type: "TEXT_MESSAGE_START", messageId, role: "assistant" };
        yield { type: "TEXT_MESSAGE_CONTENT", messageId, delta: `Built a read-only ${widget.kind.replaceAll("_", " ")} for the last ${widget.days} days. Snapshot: ${widget.as_of}. ${widget.replayed ? "Includes REPLAY records; no live payment proof." : "No payment actions are available."}` };
        yield { type: "TEXT_MESSAGE_END", messageId };
        yield { type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId };
      } catch (error) {
        if (context.signal?.aborted) return;
        const message = error instanceof Error ? error.message : "Treasury Analyst unavailable.";
        showError(message);
        yield { type: "RUN_ERROR", message };
      }
    },
  };
}
