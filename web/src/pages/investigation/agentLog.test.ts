// happy-dom (not node) so the locale is deterministic: the reducer localizes
// banners via initialLocale(), which reads navigator.language — present and
// varying across Node versions/CI, absent locally. Pin it explicitly below so
// these assertions don't depend on the ambient environment (#160).
// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";

import type { AgentEvent } from "../../events";
import {
  EMPTY_LOG,
  type AgentLog,
  type AgentMetricsState,
  formatMetrics,
  isToolRunning,
  logFromMessages,
  turnsFromEntry,
  turnPhase,
  reduceAgent,
  tokensPerSec,
} from "./agentLog";

// Pin the locale so banner assertions are environment-independent (the reducer
// reads it via initialLocale()).
beforeEach(() => localStorage.setItem("ws.locale", "zh-TW"));

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

describe("turnPhase (wait-state selector)", () => {
  const upMetrics: AgentMetricsState = { phase: "up", promptTokens: 256, completionTokens: 0, elapsedMs: 0 };
  const streaming = (over: Partial<AgentLog> = {}): AgentLog => ({ ...EMPTY_LOG, streaming: true, ...over });

  const downMetrics: AgentMetricsState = { phase: "down", promptTokens: 256, completionTokens: 3, elapsedMs: 500 };

  it("is 'prep' while streaming before any metrics arrive (backend hand-off)", () => {
    expect(turnPhase(streaming())).toBe("prep");
  });

  it("is 'thinking' once the model streams reasoning but no answer content yet", () => {
    const base = fold([{ type: "message_delta", text: "Let me think", reasoning: true }]);
    expect(turnPhase({ ...base, streaming: true, metrics: downMetrics })).toBe("thinking");
  });

  it("is 'idle' when no turn is in flight", () => {
    expect(turnPhase(EMPTY_LOG)).toBe("idle");
    expect(turnPhase({ ...EMPTY_LOG, metrics: downMetrics })).toBe("idle");
  });

  it("is 'waiting' when the prompt is with the model but no token has streamed", () => {
    expect(turnPhase(streaming({ metrics: upMetrics }))).toBe("waiting");
  });

  it("is 'answering' once visible answer content streams", () => {
    const base = fold([
      { type: "message_delta", text: "Thinking", reasoning: true },
      { type: "message_delta", text: "Here is the answer" },
    ]);
    expect(turnPhase({ ...base, streaming: true, metrics: downMetrics })).toBe("answering");
  });

  it("stays 'waiting' on a fresh turn even if a previous answer is in the log", () => {
    const base = fold([{ type: "message_delta", text: "old answer" }]);
    const fresh: AgentLog = {
      ...base,
      streaming: true,
      metrics: upMetrics,
      entries: [...base.entries, { kind: "message", message: { role: "user", content: "new q" } }],
    };
    expect(turnPhase(fresh)).toBe("waiting");
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

  it("prefers the full display over the cleaned output on tool_end (#62)", () => {
    // A command that exits 0 but wrote to stderr: the cleaned output drops
    // the stderr, but ToolEnd.display keeps it. The card must show the full
    // version so the error the user saw stream live doesn't vanish.
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: {} },
      {
        type: "tool_end",
        call_id: "c1",
        output: "Tool `exec` returned (exit_code=0):\ndone",
        display: "Tool `exec` returned (exit_code=0):\ndone\n--- stderr ---\nERROR: boom",
      },
    ]);
    const e = log.entries[0];
    if (e?.kind === "tool_call") {
      expect(e.call.output).toContain("ERROR: boom");
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("falls back to the cleaned output when tool_end has no display (#62)", () => {
    const log = fold([
      { type: "tool_start", call_id: "c1", name: "exec", args: {} },
      { type: "tool_end", call_id: "c1", output: "Tool `exec` returned (exit_code=0):\nok" },
    ]);
    const e = log.entries[0];
    if (e?.kind === "tool_call") {
      expect(e.call.output).toBe("Tool `exec` returned (exit_code=0):\nok");
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
      expect(e.call.parseError).toContain("close the brace");
      expect(e.call.parseError).toContain("{"); // the model's raw emission is shown
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

  it("surfaces the model's actual bad args (raw) so the user sees the mistake", () => {
    // #76 transparency: the user has a right to see WHAT the model got wrong.
    const log = fold([
      {
        type: "tool_call_parse_error",
        hint: "re-send valid JSON",
        raw: '{"path": ./hello.md"}',
      },
    ]);
    const banner = log.entries[0];
    if (banner?.kind !== "banner") throw new Error("expected banner");
    expect(banner.text).toContain("re-send valid JSON");
    expect(banner.text).toContain('{"path": ./hello.md"}'); // the model's emission
  });

  it("truncates a huge raw emission so the banner can't blow up", () => {
    const big = "x".repeat(500);
    const log = fold([{ type: "tool_call_parse_error", hint: "bad", raw: big }]);
    const banner = log.entries[0];
    if (banner?.kind !== "banner") throw new Error("expected banner");
    expect(banner.text).toContain("…");
    expect(banner.text.length).toBeLessThan(300); // not the full 500-char blob
  });

  it("never silently drops a parse error when its call has no card yet", () => {
    // call_id is set but no matching tool_call entry exists → must STILL banner,
    // otherwise the retry is invisible to the user (#76).
    const log = fold([
      { type: "tool_call_parse_error", call_id: "ghost", hint: "bad json", raw: "{" },
    ]);
    expect(log.entries).toMatchObject([{ kind: "banner" }]);
    const banner = log.entries[0];
    if (banner?.kind === "banner") expect(banner.text).toContain("bad json");
  });

  it("includes max_turns banner and clears streaming", () => {
    const log = fold([{ type: "max_turns_exceeded", turns: 12 }], { ...EMPTY_LOG, streaming: true });
    expect(log.streaming).toBe(false);
    // #160: de-jargoned ("turns" → 回合) and localized, still carries the count.
    expect(log.entries.some((e) => e.kind === "banner" && /回合上限（12）/.test(e.text))).toBe(true);
  });

  it("#160: the idle-restart banner describes behavior, not sandbox/exec internals", () => {
    const log = fold([{ type: "sandbox_killed_idle" }]);
    const b = log.entries.find((e) => e.kind === "banner");
    if (b?.kind !== "banner") throw new Error("expected a banner entry");
    expect(b.text).not.toMatch(/sandbox/i);
    // #171: sandbox → 執行環境 / execution environment (was 工作環境 / workspace).
    expect(b.text).toMatch(/執行環境|execution environment/);
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

  it("reloads a tool message preferring its full display over the cleaned content (#62)", () => {
    const log = logFromMessages([
      {
        role: "tool",
        content: "Tool `exec` returned (exit_code=0):\ndone",
        tool_display: "Tool `exec` returned (exit_code=0):\ndone\n--- stderr ---\nERROR: boom",
        tool_name: "exec",
        tool_call_id: "c1",
      },
    ]);
    const tool = log.entries[0];
    if (tool?.kind === "tool_call") {
      expect(tool.call.output).toContain("ERROR: boom");
    } else {
      throw new Error("expected tool_call");
    }
  });

  it("carries citations from a persisted ask_knowledge_base tool message into the ToolCallView", () => {
    // The BE attaches the KB sub-agent's resolved [n] citations onto the
    // role=tool message produced by ask_knowledge_base. Hydration must
    // surface them on the ToolCallView so the FE can render reference cards
    // under the tool card (same UX as direct KB chat).
    const log = logFromMessages([
      {
        role: "tool",
        content: "answer with [1]",
        tool_name: "ask_knowledge_base",
        tool_call_id: "c1",
        tool_args: { question: "why drift?" },
        citations: [
          {
            marker: 1,
            collection_id: "col",
            document_id: "doc",
            filename: "reflow-spec.md",
            start: 0,
            end: 50,
            source_chunk_ids: ["ck"],
            snippet: "Zone 3 setpoint…",
          },
        ],
      },
    ]);
    const tool = log.entries[0];
    if (tool?.kind === "tool_call") {
      expect(tool.call.citations).toHaveLength(1);
      expect(tool.call.citations?.[0]?.filename).toBe("reflow-spec.md");
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

describe("logFromMessages — persisted error markers (#37)", () => {
  it("renders a saved error message as a banner so a reloaded thread shows the failure", () => {
    const log = logFromMessages([
      { role: "user", author: "alice", content: "diagnose" },
      { role: "assistant", author: "RCA Agent", content: "Looking at the log" },
      { role: "error", content: "APIConnectionError: refused", error_kind: "error" },
    ]);
    expect(log.entries).toHaveLength(3);
    const last = log.entries[2];
    if (last?.kind === "banner") {
      expect(last.text).toContain("refused");
    } else {
      throw new Error("expected a banner entry for the error message");
    }
    // The partial answer before it survives.
    expect(log.entries[1]?.kind).toBe("message");
  });
});

describe("turnsFromEntry — undo math (#38)", () => {
  it("counts user turns from a given entry to the end", () => {
    const log = logFromMessages([
      { role: "user", content: "q1" },
      { role: "assistant", content: "a1" },
      { role: "user", content: "q2" },
      { role: "tool", content: "t", tool_name: "exec", tool_call_id: "c" },
      { role: "assistant", content: "a2" },
      { role: "user", content: "q3" },
      { role: "assistant", content: "a3" },
    ]);
    // Entry indices: 0=user,1=asst,2=user,3=tool,4=asst,5=user,6=asst
    expect(turnsFromEntry(log.entries, 5)).toBe(1); // last turn only
    expect(turnsFromEntry(log.entries, 2)).toBe(2); // q2 + q3
    expect(turnsFromEntry(log.entries, 0)).toBe(3); // all
    // From a non-user entry (the tool at 3) it still counts later user turns.
    expect(turnsFromEntry(log.entries, 3)).toBe(1);
  });
});

describe("repetition stop (#113)", () => {
  it("flags the current assistant message so the FE shows a notice", () => {
    let log: AgentLog = EMPTY_LOG;
    log = reduceAgent(log, { type: "message_delta", text: "Good answer. loopy loopy loopy" });
    log = reduceAgent(log, { type: "repetition_stopped", loop_length: 18, channel: "content" });
    const last = log.entries[log.entries.length - 1];
    expect(last.kind).toBe("message");
    if (last.kind === "message") {
      // Decision "b": the repeats stay visible live; only the flag is added.
      expect(last.message.content).toContain("loopy");
      expect(last.message.stopped_reason).toBe("repetition");
    }
  });
});

describe("workflow step events in the feed (#100 observability)", () => {
  it("renders step_started as a visible running step entry", () => {
    // Deterministic phases (e.g. commit: ingest each file) used to look frozen:
    // these events arrived on the SSE but the reducer dropped them. Now they
    // show live movement.
    const log = fold([{ type: "step_started", phase: "commit", name: "ingest", key: "report.md" }]);
    const last = log.entries[log.entries.length - 1];
    expect(last.kind).toBe("step");
    if (last.kind === "step") {
      expect(last.step.phase).toBe("commit");
      expect(last.step.name).toBe("ingest");
      expect(last.step.key).toBe("report.md");
      expect(last.step.status).toBe("running");
    }
  });

  it("step_passed transitions the matching running step in place (one entry)", () => {
    const log = fold([
      { type: "step_started", phase: "commit", name: "ingest", key: "a.md" },
      { type: "step_passed", phase: "commit", name: "ingest", key: "a.md" },
    ]);
    const steps = log.entries.filter((e) => e.kind === "step");
    expect(steps).toHaveLength(1); // updated in place, not a second line
    const s = steps[0];
    if (s.kind === "step") expect(s.step.status).toBe("passed");
  });

  it("step_failed transitions in place and carries the reason", () => {
    const log = fold([
      { type: "step_started", phase: "classify", name: "classify_a", key: "a.md" },
      { type: "step_failed", phase: "classify", name: "classify_a", key: "a.md", reason: "bad collection" },
    ]);
    const steps = log.entries.filter((e) => e.kind === "step");
    expect(steps).toHaveLength(1);
    const s = steps[0];
    if (s.kind === "step") {
      expect(s.step.status).toBe("failed");
      expect(s.step.reason).toBe("bad collection");
    }
  });

  it("step_skipped (cached, no preceding started) renders its own line", () => {
    // Cache hits emit StepSkipped directly without StepStarted (engine.py).
    const log = fold([{ type: "step_skipped", phase: "commit", name: "ingest", key: "a.md" }]);
    const steps = log.entries.filter((e) => e.kind === "step");
    expect(steps).toHaveLength(1);
    const s = steps[0];
    if (s.kind === "step") expect(s.step.status).toBe("skipped");
  });

  it("step_retrying flags the running step with its reason (then a later pass updates it)", () => {
    const log = fold([
      { type: "step_started", phase: "glossary", name: "glossary", key: "" },
      { type: "step_retrying", phase: "glossary", name: "glossary", key: "", reason: "empty file" },
    ]);
    const steps = log.entries.filter((e) => e.kind === "step");
    expect(steps).toHaveLength(1);
    const s = steps[0];
    if (s.kind === "step") {
      expect(s.step.status).toBe("retrying");
      expect(s.step.reason).toBe("empty file");
    }
  });

  it("phase_entered renders a phase divider in the feed", () => {
    const log = fold([{ type: "phase_entered", phase: "commit" }]);
    const last = log.entries[log.entries.length - 1];
    expect(last.kind).toBe("phase");
    if (last.kind === "phase") expect(last.phase).toBe("commit");
  });

  it("step_output streams live stdout onto the running step (#178)", () => {
    // A long deterministic step (e.g. a sandbox command) streams its stdout chunk
    // by chunk so it shows movement instead of looking dead.
    const log = fold([
      { type: "step_started", phase: "build", name: "compile", key: "" },
      { type: "step_output", phase: "build", name: "compile", key: "", text: "line 1\n" },
      { type: "step_output", phase: "build", name: "compile", key: "", text: "line 2\n" },
    ]);
    const steps = log.entries.filter((e) => e.kind === "step");
    expect(steps).toHaveLength(1); // folded onto the one running step, not new lines
    const s = steps[0];
    if (s.kind === "step") {
      expect(s.step.status).toBe("running");
      expect(s.step.liveOutput).toBe("line 1\nline 2\n");
    }
  });
});
