// SSE event unions. Mirrors src/workspace_app/api/events.py (AgentEvent)
// and the CellEvent block from §7 of plan-backend. Keep field names in
// sync — see docs/contract.md §3.
//
// Variants tagged `[anticipated]` are not yet emitted by the backend.
// They are listed in docs/contract.md §3.1 / §3.2 with their status.

/* ------------------------------------------------------------------ */
/* AgentEvent — POST /investigations/{id}/messages                     */
/* ------------------------------------------------------------------ */

export type MessageDelta = {
  type: "message_delta";
  text: string;
  /**
   * When true, append to the reasoning channel (LLM thinking / chain-of-thought)
   * instead of the visible assistant content. FE renders reasoning collapsed.
   */
  reasoning?: boolean;
};

export type ToolStart = {
  type: "tool_start";
  call_id: string;
  name: string;
  args: Record<string, unknown>;
};

export type ToolEnd = { type: "tool_end"; call_id: string; output: string };

export type RunDone = { type: "done" };

export type RunError = { type: "error"; message: string };

export type RunCancelled = { type: "run_cancelled" };

/** [anticipated] — contract §3.1 deferred, not emitted yet. */
export type SandboxKilledIdle = { type: "sandbox_killed_idle" };

export type ToolCallParseError = {
  type: "tool_call_parse_error";
  hint: string;
  call_id?: string;
  raw?: string;
};

export type MaxTurnsExceeded = { type: "max_turns_exceeded"; turns: number };

export type AgentEvent =
  | MessageDelta
  | ToolStart
  | ToolEnd
  | RunDone
  | RunError
  | RunCancelled
  | SandboxKilledIdle
  | ToolCallParseError
  | MaxTurnsExceeded;

/** Terminal events close the SSE stream and re-enable the composer. */
export function isTerminal(ev: AgentEvent): boolean {
  return (
    ev.type === "done" ||
    ev.type === "error" ||
    ev.type === "run_cancelled" ||
    ev.type === "max_turns_exceeded"
  );
}

/* ------------------------------------------------------------------ */
/* CellEvent — POST /investigations/{id}/notebooks/{path}/cells/{idx}/execute
/* ------------------------------------------------------------------ */

export type CellStream = {
  type: "cell_stream";
  stream: "stdout" | "stderr";
  text: string;
};

export type CellDisplayData = {
  type: "cell_display_data";
  /** Mime bundle keyed by mime type. image/png is base64. */
  data: Record<string, string>;
};

export type CellError = {
  type: "cell_error";
  ename: string;
  evalue: string;
  traceback: string[];
};

export type CellDone = {
  type: "cell_done";
  execution_count: number;
};

export type CellEvent = CellStream | CellDisplayData | CellError | CellDone;

export function isCellTerminal(ev: CellEvent): boolean {
  return ev.type === "cell_done";
}
