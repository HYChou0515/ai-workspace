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

// `display` (#62): the FULL result (a successful command's stderr kept) when
// it differs from `output` (the cleaned, LLM-facing result). The FE renders
// `display` when present so an error the user saw stream live doesn't vanish
// from the final card; absent ⇒ render `output`.
export type ToolEnd = { type: "tool_end"; call_id: string; output: string; display?: string };

/** Incremental stdout from a still-running tool (e.g. a long exec). call_id
 * may be empty — then it attaches to the latest running tool. */
export type ToolLog = { type: "tool_log"; call_id: string; text: string };

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

/** #113: the model degenerated into a repetition loop and the turn was stopped.
 * The repeated text already streamed live (the user sees the model misbehaved);
 * the persisted message is truncated by `loop_length` trailing chars on
 * `channel`. A `done` follows. Mirrors api/events.py RepetitionStopped. */
export type RepetitionStopped = {
  type: "repetition_stopped";
  loop_length: number;
  channel: "content" | "reasoning";
};

/** Live token telemetry for the turn. phase: "up" sending the prompt,
 * "down" streaming the reply (counts tick live, approx), "final" exact
 * usage on completion. Mirrors api/events.py AgentMetrics. */
export type AgentMetrics = {
  type: "agent_metrics";
  phase: "up" | "down" | "final";
  prompt_tokens: number;
  completion_tokens: number;
  elapsed_ms: number;
};

/** #43: a human message posted to a SHARED investigation, broadcast on the
 * per-investigation stream so every viewer sees who said what — live, before
 * the agent turn it triggers. Broadcast-only (GET /investigations/{id}/stream). */
export type UserMessage = {
  type: "user_message";
  author: string;
  content: string;
  created_at: number;
};

/** #43: a workspace file changed (a human wrote/moved/deleted it), broadcast so
 * other viewers refetch (last-write-wins). Broadcast-only. */
export type FileChanged = {
  type: "file_changed";
  path: string;
  by: string;
  kind: "written" | "moved" | "copied" | "deleted" | "dir_created";
};

/* ------------------------------------------------------------------ */
/* Workflow run events (#100, manual §12) — phase/step observability.   */
/* Ride the same per-item stream; the FE overlays them on the manifest  */
/* phase skeleton. Mirrors workflow/events.py.                          */
/* ------------------------------------------------------------------ */

/** A new workflow phase began (the first step carrying this `phase` ran). */
export type PhaseEntered = { type: "phase_entered"; phase: string };

/** A step began executing (not a cache skip). `key` is the loop element. */
export type StepStarted = { type: "step_started"; phase: string; name: string; key?: string };

/** A step's gate passed; its artifact is journaled. */
export type StepPassed = { type: "step_passed"; phase: string; name: string; key?: string };

/** A step aborted — its gate failed after all retries (`reason` = why). */
export type StepFailed = {
  type: "step_failed";
  phase: string;
  name: string;
  reason?: string;
  key?: string;
};

/** A step was skipped — its artifact exists with a matching input-hash (§9). */
export type StepSkipped = { type: "step_skipped"; phase: string; name: string; key?: string };

/** A step's gate failed but retries remain — `reason` is fed back. */
export type StepRetrying = {
  type: "step_retrying";
  phase: string;
  name: string;
  reason?: string;
  key?: string;
};

/** The run suspended at a `human_gate` (manual §10) — the FE renders the
 * decision card. Terminal for the run task (resumed via the decisions endpoint). */
export type AwaitingHuman = { type: "awaiting_human"; phase: string; title: string };

export type WorkflowEvent =
  | PhaseEntered
  | StepStarted
  | StepPassed
  | StepFailed
  | StepSkipped
  | StepRetrying
  | AwaitingHuman;

export type AgentEvent =
  | MessageDelta
  | ToolStart
  | ToolEnd
  | ToolLog
  | RunDone
  | RunError
  | RunCancelled
  | SandboxKilledIdle
  | ToolCallParseError
  | MaxTurnsExceeded
  | RepetitionStopped
  | AgentMetrics
  | UserMessage
  | FileChanged
  | WorkflowEvent;

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
