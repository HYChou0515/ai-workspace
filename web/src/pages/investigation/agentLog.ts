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
import { initialLocale, translate } from "../../lib/i18n";

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

/** One workflow step's live state in the chat feed (#100 observability). A
 * deterministic phase (e.g. commit: ingest each file) emits these so it shows
 * movement instead of looking frozen; a step transitions running → its terminal
 * status in place, mirroring ToolCallView. */
export type StepView = {
  phase: string;
  name: string;
  key?: string;
  status: "running" | "passed" | "failed" | "skipped" | "retrying";
  /** Why a step failed / is retrying — surfaced on the line. */
  reason?: string;
  /** Live stdout streamed while a deterministic step runs (#178, step_output) —
   * accumulated chunk by chunk so a long command shows movement. */
  liveOutput?: string;
};

export type AgentEntry =
  | { kind: "message"; message: Message; at?: number }
  | { kind: "tool_call"; call: ToolCallView }
  | { kind: "mention"; by: string; users: string[]; note: string; at?: number }
  | { kind: "step"; step: StepView; at?: number }
  | { kind: "phase"; phase: string; at?: number }
  | { kind: "banner"; text: string; at?: number };

export type AgentLog = {
  entries: AgentEntry[];
  /** True while the SSE stream is open. */
  streaming: boolean;
  /** Who started the turn that is streaming, or null when nothing is running.
   *
   * A shared item runs one turn at a time, but messages QUEUE server-side — they
   * do not cancel each other (#43). Locking every viewer's composer therefore
   * took away something the backend was happy to accept, and handed a spectator
   * a UI indistinguishable from broken: a spinner they did not start and a box
   * they could not type in. Separating the two cases needs this. */
  streamingBy: string | null;
  /** Non-null when the last terminal was an error. */
  error: string | null;
  /** Live token telemetry for the current turn (null until first event). */
  metrics: AgentMetricsState | null;
  /** #249/#131: set when the model failed over mid-turn — a transient "switched"
   * notice shown while we wait for the next model's first token. NEVER persisted
   * (cleared at each turn start); reset on reload. */
  failover: { at: number } | null;
  /** #492 P11: set while a cold sandbox is being restored before the turn runs —
   * a transient "還原中 N/M" line instead of a blank running card. NEVER persisted
   * (cleared once the model starts / at each turn start); reset on reload. */
  restore: { done: number; total: number } | null;
};

export const EMPTY_LOG: AgentLog = {
  entries: [],
  streaming: false,
  streamingBy: null,
  error: null,
  metrics: null,
  failover: null,
  restore: null,
};

/** How long after asking we still believe a reply is on its way.
 *
 * Generous next to the server-side give-up deadline plus retries, but finite:
 * threads that died before a turn was guaranteed to end (a hard kill, a crash,
 * anything predating that guarantee) sit in the store ending on a user message
 * forever, and without a bound every mount of one would claim "replying…" and
 * start a poll that can never terminate. An UNSTAMPED message is old data by
 * definition — the timestamp predates the field — so it never counts as live. */
const AWAITING_REPLY_MAX_MS = 30 * 60_000;

const isRecent = (at: number | null | undefined): boolean =>
  at != null && Date.now() - at < AWAITING_REPLY_MAX_MS;

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
  // A thread whose LAST message is the user's is a thread whose reply has not
  // landed — so hydrating it means a turn is still running server-side.
  //
  // Hard-coding `false` here rendered a reload mid-turn as a completely idle UI:
  // the header said "your turn", the composer unlocked, the spinner vanished,
  // and — worst — the cross-pod store-poll is gated on `streaming`, so the very
  // recovery that would have surfaced the reply was switched off too. All while
  // the turn kept burning tokens.
  //
  // The signal is only sound because a turn now always ends in SOMETHING
  // persisted (an answer, an error, a cancellation) whether the provider hangs,
  // the requester disconnects, the pod rolls or the store write fails — so it
  // cannot stick on forever.
  const last = messages[messages.length - 1];
  const awaitingReply =
    last !== undefined && last.role === "user" && isRecent(last.created_at);
  return {
    entries,
    streaming: awaitingReply,
    // The thread itself says who is waiting: the trailing user message is the
    // one whose reply has not landed.
    streamingBy: awaitingReply ? (last?.author ?? null) : null,
    error: null,
    metrics: null,
    failover: null,
    restore: null,
  };
}

/** Entries that carry the conversation itself — the ones the backend persists.
 * `step` / `phase` / live banners are stream-only chrome and are deliberately
 * NOT counted, so their presence can't disguise a store that is behind. */
const CONTENT_KINDS = new Set(["message", "tool_call", "mention"]);

const contentCount = (entries: readonly AgentEntry[]) =>
  entries.filter((e) => CONTENT_KINDS.has(e.kind)).length;

/**
 * Fold a persisted thread into the live log WITHOUT deleting what only the
 * stream knows.
 *
 * A turn is persisted ONCE, at its end, so mid-turn the stored thread holds
 * nothing of the answer being streamed. Replacing the log with it — which every
 * re-hydrate used to do — therefore deleted exactly what the user was reading.
 * That is the "the response disappears" report, and it bites hardest on a STUCK
 * turn: a long silence is precisely when a connection gets cut and a re-hydrate
 * runs. The same wholesale replace also nulled `error` and dropped the
 * cancelled / max-turns / repetition banners, so the explanation for why a turn
 * stopped survived about one frame.
 *
 * Two rules:
 *  - the store wins only once it has caught up (it is authoritative for a
 *    FINISHED turn — it alone carries the BE-attached citations);
 *  - state the store cannot express — the turn error and stream-only banners —
 *    is carried across either way.
 *
 * Use {@link logFromMessages} directly only where a smaller thread is the POINT
 * (initial hydration, undo).
 */
export function reconcileSnapshot(
  prev: AgentLog,
  thread: { messages: readonly Message[] },
): AgentLog {
  const snap = logFromMessages(thread.messages);
  // The store is behind: keep the screen, and let a later event or poll settle it.
  if (contentCount(snap.entries) < contentCount(prev.entries)) {
    return { ...prev, error: prev.error };
  }
  // Re-attach stream-only banners the persisted thread has no way to hold.
  const carried = prev.entries.filter(
    (e) =>
      e.kind === "banner" &&
      !snap.entries.some((s) => s.kind === "banner" && s.text === e.text),
  );
  return {
    ...snap,
    entries: [...snap.entries, ...carried],
    error: prev.error ?? snap.error,
  };
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

/** Index of the running step matching (phase, name, key) — so a terminal step
 * event updates the line started earlier instead of pushing a duplicate. */
function findStep(entries: AgentEntry[], phase: string, name: string, key?: string): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (
      e &&
      e.kind === "step" &&
      e.step.phase === phase &&
      e.step.name === name &&
      e.step.key === key
    ) {
      return i;
    }
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
        // The "up" tick opens a fresh turn — drop any stale failover / restore
        // notice from the previous one so it can't bleed into this turn (#249/#492).
        failover: ev.phase === "up" ? null : log.failover,
        restore: ev.phase === "up" ? null : log.restore,
        metrics: {
          phase: ev.phase,
          promptTokens: ev.prompt_tokens,
          completionTokens: ev.completion_tokens,
          elapsedMs: ev.elapsed_ms,
        },
      };

    case "failover_switch":
      // #249/#131: ephemeral — record that the model switched so TurnStatus can
      // show a transient "model busy, switched" line while the next model warms
      // up. NOT pushed to `entries`: it never enters the transcript.
      return { ...log, failover: { at: now } };

    case "restore_progress":
      // #492 P11: ephemeral — record the cold-wake restore's (done, total) so
      // TurnStatus shows "還原中 N/M" instead of a blank running card. NOT pushed
      // to `entries`: it never enters the transcript. Cleared once the turn
      // resumes real output (message/tool) or a fresh turn starts.
      return { ...log, restore: { done: ev.done, total: ev.total } };

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
      // #492 P11: the model is producing output ⇒ any cold-wake restore is over.
      return { ...log, entries, restore: null };
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
      // #492 P11: the tool that woke the sandbox has finished ⇒ restore is over.
      return { ...log, entries, restore: null };
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
      // #492 P11: the woken tool is now streaming output ⇒ restore is over.
      return { ...log, entries, restore: null };
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
        text: translate(initialLocale(), "banner.sandboxIdle"),
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

    case "max_turns_exceeded": {
      const text = translate(initialLocale(), "banner.maxTurns", { turns: ev.turns });
      entries.push({ kind: "banner", at: now, text });
      return { ...log, entries, streaming: false, streamingBy: null, error: text };
    }

    case "run_cancelled":
      entries.push({ kind: "banner", at: now, text: translate(initialLocale(), "banner.cancelled") });
      return { ...log, entries, streaming: false, streamingBy: null };

    case "error":
      return { ...log, entries, streaming: false, streamingBy: null, error: ev.message };

    case "done":
      return { ...log, entries, streaming: false, streamingBy: null };

    case "user_message":
      // #43: a human message on the shared investigation, broadcast to every
      // viewer. A turn is now in flight — flip `streaming` so the spinner
      // shows for everyone, not just the sender.
      entries.push({
        kind: "message",
        at: ev.created_at || now,
        message: { role: "user", author: ev.author, content: ev.content },
      });
      return { ...log, entries, streaming: true, streamingBy: ev.author ?? null };

    case "file_changed":
      // #43: a workspace file changed — a side effect handled in the hook
      // (refetch the file tree), not folded into the agent log.
      return log;

    case "step_started":
      // #100 observability: show deterministic-phase movement in the feed (these
      // events arrive on the same SSE; previously dropped). The step transitions
      // in place to its terminal status, like a tool call.
      entries.push({
        kind: "step",
        at: now,
        step: { phase: ev.phase, name: ev.name, key: ev.key, status: "running" },
      });
      return { ...log, entries };

    case "step_passed":
    case "step_failed":
    case "step_retrying": {
      // Transition the matching running step in place (one line per step). A
      // terminal event with no preceding start (shouldn't happen, but be safe)
      // pushes its own line so nothing is silently dropped.
      const status =
        ev.type === "step_passed" ? "passed" : ev.type === "step_failed" ? "failed" : "retrying";
      const reason = ev.type === "step_passed" ? undefined : ev.reason;
      const idx = findStep(entries, ev.phase, ev.name, ev.key);
      const e = idx >= 0 ? entries[idx] : undefined;
      if (e && e.kind === "step") {
        entries[idx] = { ...e, step: { ...e.step, status, reason } };
      } else {
        entries.push({
          kind: "step",
          at: now,
          step: { phase: ev.phase, name: ev.name, key: ev.key, status, reason },
        });
      }
      return { ...log, entries };
    }

    case "step_output": {
      // #178: stream a deterministic step's stdout onto its running line so a long
      // command shows movement instead of looking dead. Ephemeral (not persisted) —
      // if no running step is found yet, drop it (the board still shows status).
      const idx = findStep(entries, ev.phase, ev.name, ev.key);
      const e = idx >= 0 ? entries[idx] : undefined;
      if (e && e.kind === "step") {
        entries[idx] = {
          ...e,
          step: { ...e.step, liveOutput: (e.step.liveOutput ?? "") + ev.text },
        };
      }
      return { ...log, entries };
    }

    case "step_skipped":
      // A cache hit (#9) — no preceding StepStarted, so it's always its own line.
      entries.push({
        kind: "step",
        at: now,
        step: { phase: ev.phase, name: ev.name, key: ev.key, status: "skipped" },
      });
      return { ...log, entries };

    case "phase_entered":
      entries.push({ kind: "phase", at: now, phase: ev.phase });
      return { ...log, entries };

    default:
      // Anything not folded into the feed (e.g. awaiting_human → the run
      // progress view's decision card) leaves the log unchanged.
      return log;
  }
}
