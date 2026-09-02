// SSE event unions. Mirrors src/workspace_app/api/events.py (AgentEvent)
// and the CellEvent block from §7 of plan-backend. Keep field names in
// sync — see docs/contract.md §3.
//
// Variants tagged `[anticipated]` are not yet emitted by the backend.
// They are listed in docs/contract.md §3.1 / §3.2 with their status.

import type { ChatGoal } from "./api/itemGoal";

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
  /** #748: the provider's OWN counts, null where it gave none. The fields above
   * stay approximate on purpose so a live line never reads "↑0 ↓0"; these are
   * what gets recorded. Only ever set on `final`. */
  measured_prompt_tokens?: number | null;
  measured_completion_tokens?: number | null;
  /** #748: time spent GENERATING (first token → last, tool gaps and TTFT
   * excluded) — the denominator tok/s needs. `elapsed_ms` is the whole turn. */
  generation_ms?: number | null;
  /** #748: the model that actually wrote this reply — under failover, not the
   * configured one. */
  model?: string | null;
  /** #739: whether the provider itself reported these counts. False when we
   * substituted an estimate — the runner does that whenever usage comes back
   * absent or 0, so the number alone cannot tell the two apart. Mirrors
   * api/events.py AgentMetrics. */
  exact?: boolean;
};

/** #249/#131: the chat model was busy/blipped before its first token, so the turn
 * switched to the next model in the preset's failover chain. Ephemeral — shown as
 * a transient status line while the turn runs, NEVER added to the transcript. The
 * raw model id is for telemetry only; the UI shows a de-jargoned notice. */
export type FailoverSwitch = {
  type: "failover_switch";
  from_model: string;
  reason?: string;
};

/** The model endpoint asked us to slow down, so the turn is holding for
 * `seconds` before retrying the SAME endpoint. Ephemeral — a transient status
 * line, NEVER added to the transcript. Waiting is the only cure for a 429, and
 * a minute of silence reads as a hung turn, so the hold has to be visible. */
export type RateLimited = {
  type: "rate_limited";
  seconds: number;
};

/** #492 P11: the item's sandbox was cold, so its durable snapshot is being
 * restored file-by-file before the turn runs. Ephemeral — a transient "還原中 N/M"
 * status line while a slow cold wake completes, NEVER added to the transcript. */
export type RestoreProgress = {
  type: "restore_progress";
  done: number;
  total: number;
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

/** #455: the live roster of an item's stream — the distinct users currently
 * subscribed. Broadcast when a viewer joins/leaves. Broadcast-only, per-pod. */
export type Presence = {
  type: "presence";
  users: string[];
};

/** #613: the agent rewrote this conversation's todo checklist (whole-list
 * replace via the `update_todos` tool). `items` is the NEW full list in order —
 * the pinned panel swaps its state wholesale. Ephemeral on the stream; the
 * durable copy is refetched on load. Mirrors api/events.py TodosUpdated. */
export type TodosUpdated = {
  type: "todos_updated";
  items: { text: string; status: "pending" | "in_progress" | "completed" }[];
};

/** #624: the request did not fit, so older history was dropped and the turn
 * retried with less. Live-only — the durable record is the `notice` message.
 * Mirrors api/events.py ContextTrimmed. */
export type ContextTrimmed = {
  type: "context_trimmed";
  kept: number;
  dropped: number;
};

/** #739: the thread outgrew the window, so this turn first spends a round trip
 * summarising the part that no longer fits. Live-only — the durable record is
 * the `summary` message. It exists so the chat does not look frozen during the
 * one pause a user has no way to anticipate. `replaced` is how many messages
 * the summary stands in for. Mirrors api/events.py Compacting. */
export type Compacting = {
  type: "compacting";
  replaced: number;
  /** The pass has finished (either way — it may have written nothing). Switches
   * the live notice off; without it the manual path, which publishes no turn
   * afterwards, would leave it standing forever. */
  done?: boolean;
};

/** #613 P3: the chat's goal changed — set / cleared / state or round moved.
 * `goal` is the panel's whole new state, or null when cleared. Mirrors
 * api/events.py GoalUpdated. */
export type GoalUpdated = {
  type: "goal_updated";
  /** The panel's whole new state — the same shape `GET /goal` returns, so the
   * live event and the hydration cannot drift apart. */
  goal: ChatGoal | null;
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

/** Live stdout from a still-running deterministic step (#178) — folded into the
 * running step row so a long command shows movement. Ephemeral (not persisted). */
export type StepOutput = {
  type: "step_output";
  phase: string;
  name: string;
  text: string;
  key?: string;
};

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

/** A steer plan is ready for review (#288, manual §10) — the FE renders the steer
 * confirm card and refetches the run for the full plan. */
export type SteerProposed = {
  type: "steer_proposed";
  instruction: string;
  rationale: string;
};

export type WorkflowEvent =
  | PhaseEntered
  | StepStarted
  | StepOutput
  | StepPassed
  | StepFailed
  | StepSkipped
  | StepRetrying
  | AwaitingHuman
  | SteerProposed;

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
  | FailoverSwitch
  | RestoreProgress
  | RateLimited
  | TodosUpdated
  | GoalUpdated
  | ContextTrimmed
  | Compacting
  | UserMessage
  | FileChanged
  | Presence
  | WorkflowEvent;

/** The transport-level broadcast sequence carried on a live SSE event (injected
 * by the server's replay buffer, see turns.py `to_sse`), or undefined. It is not
 * a domain field — read it here rather than widening every member of the union. */
export function eventSeq(ev: AgentEvent): number | undefined {
  return (ev as { seq?: number }).seq;
}

/** The event's IDENTITY, minted where it was published and the same on every
 * pod it reaches — so a viewer recognises a re-delivery however it arrived.
 *
 * `seq` cannot serve: each pod numbers its own broadcast, so one event is #7
 * here and #3 there, and a reconnect that lands on another pod resumes in a
 * numbering that was never its own. Like `seq`, this is transport, not domain —
 * read here rather than widening every member of the union. `undefined` from a
 * backend that predates it. */
export function eventId(ev: AgentEvent): string | undefined {
  return (ev as { id?: string }).id;
}

/** Events that mean an answer is being produced RIGHT NOW.
 *
 * An allow-list, deliberately. The broadcast carries plenty that has nothing to
 * do with a turn — `file_changed` on any editor save or entity write,
 * `todos_updated`, `goal_updated`, `presence` — and reading "not presence" as
 * "a turn is running" is how an idle window that blinked came to claim it had
 * missed part of an answer, with no turn to ever take the claim back. A new
 * event type therefore defaults to "not turn progress": the cost of forgetting
 * to add one here is a warning we don't show, which is the cheaper mistake.
 *
 * A retry notice arrives as either `tool_call_parse_error` (in this list, so it
 * arms) or `RunError` — wire type `"error"`, which `isTerminal` treats as an
 * ending even though the turn continues. That is pre-existing and unchanged
 * here; stating it plainly because an earlier version of this comment claimed
 * the opposite. */
const TURN_PROGRESS: ReadonlySet<string> = new Set([
  // A turn is starting: broadcast only after admission and after the message is
  // persisted, so unlike `file_changed` it cannot fire without one. The window
  // to the first delta is the model's time-to-first-token — seconds on a local
  // model — and a stream lost inside it loses the whole answer, so this is the
  // one non-output event where a hole is genuinely possible.
  "user_message",
  "message_delta",
  "tool_start",
  "tool_end",
  "tool_log",
  "agent_metrics",
  "tool_call_parse_error",
  "repetition_stopped",
  "failover_switch",
  "restore_progress",
  "rate_limited",
  "context_trimmed",
]);

export function isTurnProgress(ev: AgentEvent): boolean {
  return TURN_PROGRESS.has(ev.type);
}

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
