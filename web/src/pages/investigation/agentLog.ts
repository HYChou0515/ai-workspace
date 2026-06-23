/**
 * Pure reducer over the agent SSE event stream. Folds AgentEvent into
 * Conversation state — keeping the UI free of streaming bookkeeping.
 *
 * Render decisions:
 *  - message_delta with reasoning=true appends to `reasoning`, else to `content`
 *  - tool_start/tool_end pair render as a single ToolCall entry
 *  - tool_call_parse_error is shown as a transient banner under the call
 */

import type { AgentEvent } from "../../events";
import type { Message, MessageCitation } from "../../api/types";

export type ToolCallView = {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done";
  output?: string;
  /** stdout streamed live while the tool is still running (tool_log). */
  liveOutput?: string;
  parseError?: string;
  /** epoch ms when the call started / finished (for duration + timestamps). */
  startedAt?: number;
  endedAt?: number;
  /** Resolved [n] citations on this tool's answer. Only set for
   * `ask_knowledge_base` tool messages on reload — the BE attaches the KB
   * sub-agent's citations onto the persisted tool message. Rendered as
   * clickable source cards under the tool card (same UX as KB chat). */
  citations?: MessageCitation[];
};

/** Live token telemetry for the current turn (Claude-Code-style ↑/↓). */
export type AgentMetricsState = {
  phase: "up" | "down" | "final";
  promptTokens: number;
  completionTokens: number;
  elapsedMs: number;
};

export type AgentEntry =
  | { kind: "message"; message: Message; at?: number }
  | { kind: "tool_call"; call: ToolCallView }
  | { kind: "mention"; by: string; users: string[]; note: string; at?: number }
  | { kind: "banner"; text: string; at?: number };

export type AgentLog = {
  entries: AgentEntry[];
  /** True while the SSE stream is open. */
  streaming: boolean;
  /** Non-null when the last terminal was an error. */
  error: string | null;
  /** Live token telemetry for the current turn (null until first event). */
  metrics: AgentMetricsState | null;
};

export const EMPTY_LOG: AgentLog = {
  entries: [],
  streaming: false,
  error: null,
  metrics: null,
};

export function logFromMessages(messages: readonly Message[]): AgentLog {
  const entries: AgentEntry[] = [];
  for (const m of messages) {
    if (m.role === "tool") {
      entries.push({
        kind: "tool_call",
        call: {
          call_id: m.tool_call_id ?? "—",
          name: m.tool_name ?? "tool",
          args: m.tool_args ?? {},
          status: "done",
          // #62: prefer the full display (success stderr kept) so a reloaded
          // card shows the error the user saw live, not the cleaned content.
          output: m.tool_display || m.content,
          startedAt: m.created_at ?? undefined,
          endedAt: m.created_at ?? undefined,
          // Carry the BE-attached citations through so an ask_knowledge_base
          // tool message reloads with its reference cards. Non-ask_kb tools
          // never set this on the BE side — empty becomes undefined.
          citations: m.citations && m.citations.length > 0 ? m.citations : undefined,
        },
      });
    } else if (m.role === "mention") {
      entries.push({
        kind: "mention",
        by: m.author ?? "",
        users: m.mentions ?? [],
        note: m.content,
        at: m.created_at ?? undefined,
      });
    } else if (m.role === "error") {
      // #37: a persisted terminal failure — rendered as a banner so a
      // reloaded thread shows the turn died (matching the live error
      // banners), rather than the message silently vanishing.
      entries.push({ kind: "banner", text: m.content, at: m.created_at ?? undefined });
    } else {
      entries.push({ kind: "message", message: m, at: m.created_at ?? undefined });
    }
  }
  return { entries, streaming: false, error: null, metrics: null };
}

/** How many whole turns to undo to remove everything from `entries[index]`
 * onward (issue #38) = the count of user-prompt entries at or after it. A
 * turn is delimited by a user message; this matches the BE's turn-count
 * semantics so "undo to here" on a user turn drops it and all later turns. */
export function turnsFromEntry(entries: readonly AgentEntry[], index: number): number {
  let n = 0;
  for (let i = index; i < entries.length; i++) {
    const e = entries[i];
    if (e && e.kind === "message" && e.message.role === "user") n++;
  }
  return n;
}

/** tok/s for the completion phase (0 until any time has elapsed). */
export function tokensPerSec(m: AgentMetricsState): number {
  return m.elapsedMs > 0 ? Math.round(m.completionTokens / (m.elapsedMs / 1000)) : 0;
}

/** True while any tool call in the log is still running (no tool_end yet). The
 * status line uses this to keep the cumulative token count visible — but
 * paused — during the tool gap, when no new metrics arrive. */
export function isToolRunning(log: AgentLog): boolean {
  return log.entries.some((e) => e.kind === "tool_call" && e.call.status === "running");
}

/** The waiting-state of an in-flight turn, derived purely from the folded log
 * (no extra events). Splits the opaque "working…" wait into legible phases so a
 * long hang shows WHERE it's stuck — and lets a viewer infer backend-slow
 * (`prep`) vs LLM-slow (`waiting`/`thinking`) from which phase lingers:
 *  - idle:      no turn in flight
 *  - prep:      streaming, but the backend hasn't emitted its first metrics yet
 *               (it's still building the turn / handing off to the model)
 *  - waiting:   the prompt is with the model, but not one token has streamed
 *               (cold-load / prefill / a busy LLM service — the long blank gap)
 *  - thinking:  the model is streaming reasoning, no answer content yet
 *  - answering: the model is streaming the visible answer */
export type TurnPhase = "idle" | "prep" | "waiting" | "thinking" | "answering";

export function turnPhase(log: AgentLog): TurnPhase {
  if (!log.streaming) return "idle";
  if (!log.metrics) return "prep";
  // The trailing assistant message is this turn's live output (a user prompt or
  // tool call ends the run, so a fresh turn has none yet — see lastAssistantIdx).
  const idx = lastAssistantIdx(log.entries);
  const entry = idx >= 0 ? log.entries[idx] : undefined;
  const msg = entry && entry.kind === "message" ? entry.message : undefined;
  if (msg && msg.content.trim().length > 0) return "answering";
  if (msg && (msg.reasoning ?? "").length > 0) return "thinking";
  return "waiting";
}

/** Claude-Code-style one-liner: ↑ prompt while sending, ↓ completion +
 * tok/s while/after receiving. While a tool runs (`toolRunning`), generation is
 * paused and no fresh metrics arrive, so keep the cumulative ↑/↓ tokens but drop
 * the would-be-stale tok/s · elapsed and flag the tool instead. */
export function formatMetrics(m: AgentMetricsState, toolRunning = false): string {
  if (toolRunning) return `↑ ${m.promptTokens} · ↓ ${m.completionTokens} tok · ⏳ running…`;
  const secs = (m.elapsedMs / 1000).toFixed(1);
  if (m.phase === "up") return `↑ ${m.promptTokens} tok · sending…`;
  return `↑ ${m.promptTokens} · ↓ ${m.completionTokens} tok · ${tokensPerSec(m)} tok/s · ${secs}s`;
}

/* ------------------------- internal helpers ------------------------- */

function findCall(entries: AgentEntry[], call_id: string): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e && e.kind === "tool_call" && e.call.call_id === call_id) return i;
  }
  return -1;
}

/** Cap the model's raw bad-args echo so a runaway emission (e.g. a huge
 * write_file content blob) can't blow up the parse-error banner. */
function truncateRaw(raw: string, max = 200): string {
  return raw.length <= max ? raw : `${raw.slice(0, max)}…`;
}

/** Index of the tool call a tool_log belongs to: the matching call_id, or
 * (when call_id is empty) the latest still-running tool call. */
function liveCallIdx(entries: AgentEntry[], call_id: string): number {
  if (call_id) return findCall(entries, call_id);
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e && e.kind === "tool_call" && e.call.status === "running") return i;
  }
  return -1;
}

function lastAssistantIdx(entries: AgentEntry[]): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (!e) continue;
    // A tool call OR a non-assistant message (e.g. the user's next prompt)
    // ends the current assistant run — so a new turn starts fresh instead
    // of appending to the previous turn's answer.
    if (e.kind === "tool_call") return -1;
    if (e.kind === "message") return e.message.role === "assistant" ? i : -1;
  }
  return -1;
}

/* ------------------------------- reducer ------------------------------- */

export function reduceAgent(log: AgentLog, ev: AgentEvent, now: number = Date.now()): AgentLog {
  const entries = [...log.entries];

  switch (ev.type) {
    case "agent_metrics":
      return {
        ...log,
        metrics: {
          phase: ev.phase,
          promptTokens: ev.prompt_tokens,
          completionTokens: ev.completion_tokens,
          elapsedMs: ev.elapsed_ms,
        },
      };

    case "message_delta": {
      const idx = lastAssistantIdx(entries);
      const last = idx >= 0 ? entries[idx] : undefined;
      if (last && last.kind === "message" && last.message.role === "assistant") {
        const m = last.message;
        const updated: Message = ev.reasoning
          ? { ...m, reasoning: (m.reasoning ?? "") + ev.text }
          : { ...m, content: m.content + ev.text };
        // Preserve the entry's original timestamp — don't drop `at` on append,
        // or the live time vanishes after the first delta.
        entries[idx] = { kind: "message", at: last.at ?? now, message: updated };
      } else {
        entries.push({
          kind: "message",
          at: now,
          message: ev.reasoning
            ? { role: "assistant", content: "", reasoning: ev.text, author: "RCA Agent" }
            : { role: "assistant", content: ev.text, author: "RCA Agent" },
        });
      }
      return { ...log, entries };
    }

    case "tool_start":
      entries.push({
        kind: "tool_call",
        call: {
          call_id: ev.call_id,
          name: ev.name,
          args: ev.args,
          status: "running",
          startedAt: now,
        },
      });
      return { ...log, entries };

    case "tool_end": {
      const idx = findCall(entries, ev.call_id);
      if (idx >= 0) {
        const e = entries[idx];
        if (e && e.kind === "tool_call") {
          entries[idx] = {
            kind: "tool_call",
            // #62: prefer the full display result (success stderr kept) so the
            // card keeps the error that streamed live instead of the cleaned
            // "exit_code=0"; absent ⇒ the cleaned output.
            call: { ...e.call, status: "done", output: ev.display || ev.output, endedAt: now },
          };
        }
      }
      return { ...log, entries };
    }

    case "tool_log": {
      const idx = liveCallIdx(entries, ev.call_id);
      if (idx >= 0) {
        const e = entries[idx];
        if (e && e.kind === "tool_call") {
          entries[idx] = {
            kind: "tool_call",
            call: { ...e.call, liveOutput: (e.call.liveOutput ?? "") + ev.text },
          };
        }
      }
      return { ...log, entries };
    }

    case "tool_call_parse_error": {
      // #76 transparency: the user has a right to see WHAT the model got wrong,
      // not just the coaching hint. Append the model's actual emission (`raw`,
      // truncated) so a malformed tool call like `{"path": ./hello.md"}` is
      // visible. Attach to the running tool card when we can find it; otherwise
      // ALWAYS push a banner — never silently drop, or the retry is invisible.
      const sent = ev.raw ? ` — the model sent: ${truncateRaw(ev.raw)}` : "";
      const text = `${ev.hint}${sent}`;
      if (ev.call_id) {
        const idx = findCall(entries, ev.call_id);
        const e = idx >= 0 ? entries[idx] : undefined;
        if (e && e.kind === "tool_call") {
          entries[idx] = { kind: "tool_call", call: { ...e.call, parseError: text } };
          return { ...log, entries };
        }
      }
      entries.push({ kind: "banner", at: now, text: `parse error: ${text}` });
      return { ...log, entries };
    }

    case "sandbox_killed_idle":
      entries.push({
        kind: "banner",
        at: now,
        text: "sandbox went idle — restarting on next exec",
      });
      return { ...log, entries };

    case "repetition_stopped": {
      // #113: the model degenerated into a loop. Decision "b" — the repeats
      // already streamed and stay visible; we only flag the current assistant
      // message so the view shows a notice (live and, via the persisted flag,
      // on reload). A `done` follows to close the stream.
      const idx = lastAssistantIdx(entries);
      const last = idx >= 0 ? entries[idx] : undefined;
      if (last && last.kind === "message" && last.message.role === "assistant") {
        entries[idx] = {
          ...last,
          message: { ...last.message, stopped_reason: "repetition" },
        };
      }
      return { ...log, entries };
    }

    case "max_turns_exceeded":
      entries.push({ kind: "banner", at: now, text: `max turns (${ev.turns}) exceeded` });
      return { ...log, entries, streaming: false, error: "max turns exceeded" };

    case "run_cancelled":
      entries.push({ kind: "banner", at: now, text: "run cancelled" });
      return { ...log, entries, streaming: false };

    case "error":
      return { ...log, entries, streaming: false, error: ev.message };

    case "done":
      return { ...log, entries, streaming: false };

    case "user_message":
      // #43: a human message on the shared investigation, broadcast to every
      // viewer. A turn is now in flight — flip `streaming` so the spinner
      // shows for everyone, not just the sender.
      entries.push({
        kind: "message",
        at: ev.created_at || now,
        message: { role: "user", author: ev.author, content: ev.content },
      });
      return { ...log, entries, streaming: true };

    case "file_changed":
      // #43: a workspace file changed — a side effect handled in the hook
      // (refetch the file tree), not folded into the agent log.
      return log;

    default:
      // #100: workflow phase/step events ride the same item stream but are
      // rendered by the run progress view (WorkflowRunSection), not the chat log.
      return log;
  }
}
