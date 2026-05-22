// Mirrors src/workspace_app/api/events.py — keep field names in sync.
//
// Variants tagged `[anticipated]` are not yet emitted by the backend.
// They are listed in plan-backend.md §4 with a `⏳` status. When the
// backend ships them, drop the comment; if a variant is renamed, this
// file must change in the same commit (see plan-frontend.md §2).

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

// [anticipated] — backend §3.2; terminal
export type RunCancelled = { type: "run_cancelled" };

// [anticipated] — backend §3.3; non-terminal, stream continues
export type SandboxKilledIdle = { type: "sandbox_killed_idle" };

// [anticipated] — backend §3.6; non-terminal, retry follows
export type ToolCallParseError = {
  type: "tool_call_parse_error";
  call_id: string;
  raw: string;
  hint: string;
};

// [anticipated] — backend §3.6; terminal
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
