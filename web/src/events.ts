// Mirrors src/workspace_app/api/events.py — keep field names in sync.

export type MessageDelta = { type: "message_delta"; text: string };
export type ToolStart = {
  type: "tool_start";
  call_id: string;
  name: string;
  args: Record<string, unknown>;
};
export type ToolEnd = { type: "tool_end"; call_id: string; output: string };
export type RunDone = { type: "done" };
export type RunError = { type: "error"; message: string };

export type AgentEvent =
  | MessageDelta
  | ToolStart
  | ToolEnd
  | RunDone
  | RunError;

/**
 * Stream events from a POST that returns text/event-stream. Yields each
 * decoded JSON event payload. Stops when the server closes the connection
 * or `signal` aborts.
 */
export async function* streamAgentEvents(
  workspaceId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const resp = await fetch(`/workspaces/${encodeURIComponent(workspaceId)}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`POST messages failed: ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE event boundary is a blank line ("\n\n").
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!chunk.startsWith("data:")) continue;
      const payload = chunk.slice("data:".length).trim();
      if (!payload) continue;
      try {
        yield JSON.parse(payload) as AgentEvent;
      } catch {
        // ignore malformed event; small-model output can be junky per
        // grill-me's small-model-reliability caveat
      }
    }
  }
}
