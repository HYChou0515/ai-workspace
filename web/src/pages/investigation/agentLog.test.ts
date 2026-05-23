import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../../events";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reduceAgent,
} from "./agentLog";

function fold(events: AgentEvent[], from: AgentLog = EMPTY_LOG): AgentLog {
  return events.reduce(reduceAgent, from);
}

describe("reduceAgent", () => {
  it("accumulates message_delta into a single assistant message", () => {
    const log = fold([
      { type: "message_delta", text: "Hello " },
      { type: "message_delta", text: "world" },
    ]);
    const first = log.entries[0];
    expect(first?.kind).toBe("message");
    if (first?.kind === "message") {
      expect(first.message.content).toBe("Hello world");
      expect(first.message.author).toBe("RCA Agent");
    }
  });

  it("routes message_delta with reasoning=true to the reasoning channel", () => {
    const log = fold([
      { type: "message_delta", text: "thinking part…", reasoning: true },
      { type: "message_delta", text: "Answer." },
    ]);
    const m = log.entries[0];
    if (m?.kind === "message") {
      expect(m.message.reasoning).toBe("thinking part…");
      expect(m.message.content).toBe("Answer.");
    } else {
      throw new Error("expected message entry");
    }
  });

  it("pairs tool_start and tool_end into a single tool_call entry", () => {
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: { cmd: ["ls"] } },
      { type: "tool_end", call_id: "c1", output: "ok" },
    ]);
    expect(log.entries).toHaveLength(1);
    const e = log.entries[0];
    expect(e?.kind).toBe("tool_call");
    if (e?.kind === "tool_call") {
      expect(e.call.status).toBe("done");
      expect(e.call.output).toBe("ok");
      expect(e.call.name).toBe("exec");
    }
  });

  it("marks streaming=false on terminal events", () => {
    expect(fold([{ type: "done" }], { ...EMPTY_LOG, streaming: true }).streaming).toBe(false);
    expect(fold([{ type: "error", message: "x" }], { ...EMPTY_LOG, streaming: true }).streaming).toBe(false);
    expect(fold([{ type: "run_cancelled" }], { ...EMPTY_LOG, streaming: true }).streaming).toBe(false);
  });

  it("captures error message", () => {
    const log = fold([{ type: "error", message: "boom" }], { ...EMPTY_LOG, streaming: true });
    expect(log.error).toBe("boom");
  });

  it("attaches a parse-error hint to the running tool call", () => {
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: { cmd: "<bad>" } },
      { type: "tool_call_parse_error", call_id: "c1", raw: "{", hint: "close the brace" },
    ]);
    const e = log.entries[0];
    if (e?.kind === "tool_call") {
      expect(e.call.parseError).toBe("close the brace");
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("pushes a banner for unattributed parse errors", () => {
    const log = fold([{ type: "tool_call_parse_error", hint: "global parse error" }]);
    expect(log.entries).toEqual([{ kind: "banner", text: "parse error: global parse error" }]);
  });

  it("includes max_turns banner and clears streaming", () => {
    const log = fold([{ type: "max_turns_exceeded", turns: 12 }], { ...EMPTY_LOG, streaming: true });
    expect(log.streaming).toBe(false);
    expect(log.entries.some((e) => e.kind === "banner" && /max turns \(12\)/.test(e.text))).toBe(true);
  });

  it("starts a new assistant message after a tool call returns", () => {
    const log = fold([
      { type: "message_delta", text: "Pre. " },
      { type: "tool_start", call_id: "c1", name: "exec", args: {} },
      { type: "tool_end", call_id: "c1", output: "out" },
      { type: "message_delta", text: "Post." },
    ]);
    const msgs = log.entries.filter((e) => e.kind === "message");
    expect(msgs).toHaveLength(2);
  });
});

describe("logFromMessages", () => {
  it("hydrates a saved conversation into log entries", () => {
    const log = logFromMessages([
      { role: "user", author: "alice", content: "hello" },
      { role: "assistant", author: "RCA Agent", content: "world" },
      { role: "tool", content: "ok", tool_name: "exec", tool_call_id: "c1" },
    ]);
    expect(log.entries).toHaveLength(3);
    expect(log.entries[0]?.kind).toBe("message");
    expect(log.entries[2]?.kind).toBe("tool_call");
  });
});
