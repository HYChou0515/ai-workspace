import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../../events";
import {
  EMPTY_LOG,
  type AgentLog,
  formatMetrics,
  isToolRunning,
  logFromMessages,
  reduceAgent,
  tokensPerSec,
} from "./agentLog";

describe("metrics formatting", () => {
  it("computes tok/s from completion tokens over elapsed", () => {
    expect(tokensPerSec({ phase: "down", promptTokens: 100, completionTokens: 60, elapsedMs: 2000 })).toBe(30);
    expect(tokensPerSec({ phase: "up", promptTokens: 100, completionTokens: 0, elapsedMs: 0 })).toBe(0);
  });
  it("shows ↑ while sending and ↑/↓ + tok/s while receiving", () => {
    expect(formatMetrics({ phase: "up", promptTokens: 256, completionTokens: 0, elapsedMs: 0 })).toMatch(/↑ 256 tok/);
    const down = formatMetrics({ phase: "down", promptTokens: 256, completionTokens: 40, elapsedMs: 1000 });
    expect(down).toContain("↑ 256");
    expect(down).toContain("↓ 40 tok");
    expect(down).toContain("40 tok/s");
  });
  it("during a tool call keeps cumulative tokens but drops the stale tok/s", () => {
    const m = { phase: "down" as const, promptTokens: 256, completionTokens: 40, elapsedMs: 1000 };
    const line = formatMetrics(m, true);
    expect(line).toContain("↑ 256");
    expect(line).toContain("↓ 40 tok");
    expect(line).toContain("running");
    expect(line).not.toContain("tok/s"); // paused — no misleading rate during the tool gap
  });
});

describe("isToolRunning", () => {
  const tool = (status: "running" | "done") => ({
    kind: "tool_call" as const,
    call: { call_id: "t1", name: "kb_search", args: {}, status },
  });
  it("is true while a tool_call entry is still running", () => {
    expect(isToolRunning({ ...EMPTY_LOG, entries: [tool("running")] })).toBe(true);
  });
  it("is false once every tool call is done (or there are none)", () => {
    expect(isToolRunning({ ...EMPTY_LOG, entries: [tool("done")] })).toBe(false);
    expect(isToolRunning(EMPTY_LOG)).toBe(false);
  });
});

function fold(events: AgentEvent[], from: AgentLog = EMPTY_LOG): AgentLog {
  // wrap so Array.reduce's index isn't passed as reduceAgent's `now`
  return events.reduce((log, ev) => reduceAgent(log, ev), from);
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

  it("appends tool_log chunks to the running tool's live output", () => {
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: { cmd: ["loop"] } },
      { type: "tool_log", call_id: "c1", text: "1s *\n" },
      { type: "tool_log", call_id: "c1", text: "2s **\n" },
    ]);
    const e = log.entries[0];
    if (e?.kind === "tool_call") {
      expect(e.call.status).toBe("running");
      expect(e.call.liveOutput).toBe("1s *\n2s **\n");
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("attaches tool_log to the latest running tool when call_id is empty", () => {
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: {} },
      { type: "tool_log", call_id: "", text: "live line\n" },
    ]);
    const e = log.entries[0];
    if (e?.kind === "tool_call") {
      expect(e.call.liveOutput).toBe("live line\n");
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("keeps live output visible after tool_end sets the final output", () => {
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: {} },
      { type: "tool_log", call_id: "c1", text: "streamed\n" },
      { type: "tool_end", call_id: "c1", output: "exit_code=0\n--- stdout ---\nstreamed\n" },
    ]);
    const e = log.entries[0];
    if (e?.kind === "tool_call") {
      expect(e.call.status).toBe("done");
      expect(e.call.output).toContain("streamed");
      expect(e.call.liveOutput).toBe("streamed\n");
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("keeps the assistant message's timestamp stable across streaming deltas", () => {
    const t = 1_700_000_000_000;
    let log = reduceAgent(EMPTY_LOG, { type: "message_delta", text: "Hel" }, t);
    log = reduceAgent(log, { type: "message_delta", text: "lo" }, t + 999);
    const m = log.entries[0];
    if (m?.kind === "message") {
      expect(m.message.content).toBe("Hello");
      expect(m.at).toBe(t); // the first delta's time, not lost on append
    } else {
      throw new Error("expected message");
    }
  });

  it("stamps each log entry with the time it was created", () => {
    const t = 1_700_000_000_000;
    const log = reduceAgent(EMPTY_LOG, { type: "message_delta", text: "hi" }, t);
    const m = log.entries[0];
    if (m?.kind === "message") expect(m.at).toBe(t);
    else throw new Error("expected message entry");

    const banner = reduceAgent(EMPTY_LOG, { type: "max_turns_exceeded", turns: 5 }, t);
    const b = banner.entries[0];
    if (b?.kind === "banner") expect(b.at).toBe(t);
    else throw new Error("expected banner entry");
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
    expect(log.entries).toMatchObject([
      { kind: "banner", text: "parse error: global parse error" },
    ]);
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

describe("reduceAgent — turn boundaries", () => {
  it("does not append a new turn's reply to the previous turn's answer", () => {
    const log = fold([
      { type: "message_delta", text: "First answer." },
      { type: "done" },
      // next turn: a fresh user prompt, then the agent's reply
      // (user message is added by useAgent, not the reducer — simulate it)
    ]);
    // simulate the user message + a new assistant delta
    const withUser: AgentLog = {
      ...log,
      entries: [
        ...log.entries,
        { kind: "message", message: { role: "user", content: "again" } },
      ],
    };
    const next = reduceAgent(withUser, { type: "message_delta", text: "Second answer." });
    const assistants = next.entries.filter(
      (e) => e.kind === "message" && e.message.role === "assistant",
    );
    expect(assistants).toHaveLength(2);
    if (assistants[0]?.kind === "message") expect(assistants[0].message.content).toBe("First answer.");
    if (assistants[1]?.kind === "message") expect(assistants[1].message.content).toBe("Second answer.");
  });
});

describe("reduceAgent — metrics + timing", () => {
  it("tracks live token metrics from agent_metrics events", () => {
    const log = fold([
      { type: "agent_metrics", phase: "up", prompt_tokens: 120, completion_tokens: 0, elapsed_ms: 0 },
      { type: "agent_metrics", phase: "down", prompt_tokens: 120, completion_tokens: 40, elapsed_ms: 800 },
      { type: "agent_metrics", phase: "final", prompt_tokens: 118, completion_tokens: 51, elapsed_ms: 1500 },
    ]);
    expect(log.metrics).toEqual({
      phase: "final",
      promptTokens: 118,
      completionTokens: 51,
      elapsedMs: 1500,
    });
  });

  it("stamps tool start/end times so duration can be shown", () => {
    let log = EMPTY_LOG;
    log = reduceAgent(log, { type: "tool_start", call_id: "c1", name: "exec", args: {} }, 1000);
    log = reduceAgent(log, { type: "tool_end", call_id: "c1", output: "ok" }, 1700);
    const e = log.entries[0];
    expect(e?.kind).toBe("tool_call");
    if (e?.kind === "tool_call") {
      expect(e.call.startedAt).toBe(1000);
      expect(e.call.endedAt).toBe(1700);
    }
  });

  it("preserves metrics across a terminal done event", () => {
    const log = fold([
      { type: "agent_metrics", phase: "final", prompt_tokens: 10, completion_tokens: 5, elapsed_ms: 200 },
      { type: "done" },
    ]);
    expect(log.streaming).toBe(false);
    expect(log.metrics?.completionTokens).toBe(5);
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

  it("restores timestamps + tool name/args so a reload keeps the full detail", () => {
    const t = 1_700_000_000_000;
    const log = logFromMessages([
      { role: "user", author: "alice", content: "hello", created_at: t },
      {
        role: "tool",
        content: "exit_code=0",
        tool_name: "exec",
        tool_call_id: "c1",
        tool_args: { cmd: ["echo", "hi"] },
        created_at: t + 500,
      },
    ]);
    const msg = log.entries[0];
    if (msg?.kind === "message") expect(msg.at).toBe(t);
    else throw new Error("expected message");
    const tool = log.entries[1];
    if (tool?.kind === "tool_call") {
      expect(tool.call.name).toBe("exec");
      expect(tool.call.args).toEqual({ cmd: ["echo", "hi"] });
      expect(tool.call.startedAt).toBe(t + 500);
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("maps a role=mention message to a mention entry", () => {
    const log = logFromMessages([
      {
        role: "mention",
        author: "alice",
        mentions: ["bob", "carol"],
        content: "please look",
        created_at: 123,
      },
    ]);
    const e = log.entries[0];
    if (e?.kind === "mention") {
      expect(e.by).toBe("alice");
      expect(e.users).toEqual(["bob", "carol"]);
      expect(e.note).toBe("please look");
    } else {
      throw new Error("expected mention");
    }
  });
});
