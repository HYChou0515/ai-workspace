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
import type { Message } from "../../api/types";

export type ToolCallView = {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done";
  output?: string;
  parseError?: string;
};

export type AgentEntry =
  | { kind: "message"; message: Message }
  | { kind: "tool_call"; call: ToolCallView }
  | { kind: "banner"; text: string };

export type AgentLog = {
  entries: AgentEntry[];
  /** True while the SSE stream is open. */
  streaming: boolean;
  /** Non-null when the last terminal was an error. */
  error: string | null;
};

export const EMPTY_LOG: AgentLog = {
  entries: [],
  streaming: false,
  error: null,
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
          args: {},
          status: "done",
          output: m.content,
        },
      });
    } else {
      entries.push({ kind: "message", message: m });
    }
  }
  return { entries, streaming: false, error: null };
}

/* ------------------------- internal helpers ------------------------- */

function findCall(entries: AgentEntry[], call_id: string): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e && e.kind === "tool_call" && e.call.call_id === call_id) return i;
  }
  return -1;
}

function lastAssistantIdx(entries: AgentEntry[]): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e && e.kind === "tool_call") return -1; // tool call ends the assistant run
    if (e && e.kind === "message" && e.message.role === "assistant") return i;
  }
  return -1;
}

/* ------------------------------- reducer ------------------------------- */

export function reduceAgent(log: AgentLog, ev: AgentEvent): AgentLog {
  const entries = [...log.entries];

  switch (ev.type) {
    case "message_delta": {
      const idx = lastAssistantIdx(entries);
      const last = idx >= 0 ? entries[idx] : undefined;
      if (last && last.kind === "message" && last.message.role === "assistant") {
        const m = last.message;
        const updated: Message = ev.reasoning
          ? { ...m, reasoning: (m.reasoning ?? "") + ev.text }
          : { ...m, content: m.content + ev.text };
        entries[idx] = { kind: "message", message: updated };
      } else {
        entries.push({
          kind: "message",
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
            call: { ...e.call, status: "done", output: ev.output },
          };
        }
      }
      return { ...log, entries };
    }

    case "tool_call_parse_error": {
      if (ev.call_id) {
        const idx = findCall(entries, ev.call_id);
        if (idx >= 0) {
          const e = entries[idx];
          if (e && e.kind === "tool_call") {
            entries[idx] = {
              kind: "tool_call",
              call: { ...e.call, parseError: ev.hint },
            };
          }
        }
      } else {
        entries.push({ kind: "banner", text: `parse error: ${ev.hint}` });
      }
      return { ...log, entries };
    }

    case "sandbox_killed_idle":
      entries.push({ kind: "banner", text: "sandbox went idle — restarting on next exec" });
      return { ...log, entries };

    case "max_turns_exceeded":
      entries.push({ kind: "banner", text: `max turns (${ev.turns}) exceeded` });
      return { ...log, entries, streaming: false, error: "max turns exceeded" };

    case "run_cancelled":
      entries.push({ kind: "banner", text: "run cancelled" });
      return { ...log, entries, streaming: false };

    case "error":
      return { ...log, entries, streaming: false, error: ev.message };

    case "done":
      return { ...log, entries, streaming: false };
  }
}
