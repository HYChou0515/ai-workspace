import { describe, expect, it } from "vitest";

import { foldTraces, type MonitorEvent } from "./monitor";

describe("foldTraces", () => {
  it("folds trace + span events into one trace with spans and summed tokens", () => {
    const events: MonitorEvent[] = [
      { kind: "trace_start", id: "t1", group_id: "inv-1", workflow_name: "Wiki maintainer" },
      {
        kind: "span_end",
        id: "s1",
        trace_id: "t1",
        span_data: { type: "generation", model: "qwen3", usage: { input_tokens: 100, output_tokens: 20 } },
      },
      { kind: "span_end", id: "s2", trace_id: "t1", span_data: { type: "function", name: "write_file" } },
      { kind: "trace_end", id: "t1", group_id: "inv-1", workflow_name: "Wiki maintainer" },
    ];
    const [t] = foldTraces(events);
    expect(t.workflowName).toBe("Wiki maintainer");
    expect(t.groupId).toBe("inv-1");
    expect(t.done).toBe(true);
    expect(t.spans.map((s) => s.type)).toEqual(["generation", "function"]);
    expect(t.spans[1].label).toBe("write_file");
    expect(t.inputTokens).toBe(100);
    expect(t.outputTokens).toBe(20);
  });

  it("dedups a span that appears in both history and the live overlap", () => {
    const span: MonitorEvent = {
      kind: "span_end",
      id: "s1",
      trace_id: "t1",
      span_data: { type: "generation", usage: { input_tokens: 5 } },
    };
    const events: MonitorEvent[] = [{ kind: "trace_start", id: "t1" }, span, span];
    expect(foldTraces(events)[0].spans).toHaveLength(1);
    expect(foldTraces(events)[0].inputTokens).toBe(5);
  });

  it("orders the newest trace first", () => {
    const events: MonitorEvent[] = [
      { kind: "trace_start", id: "t1", workflow_name: "A" },
      { kind: "trace_start", id: "t2", workflow_name: "B" },
    ];
    expect(foldTraces(events).map((t) => t.traceId)).toEqual(["t2", "t1"]);
  });

  it("attaches a span whose trace_start it hasn't seen (group inherited)", () => {
    const events: MonitorEvent[] = [
      {
        kind: "span_end",
        id: "s1",
        trace_id: "t1",
        group_id: "inv-9",
        span_data: { type: "function", name: "exec" },
      },
    ];
    const [t] = foldTraces(events);
    expect(t.groupId).toBe("inv-9");
    expect(t.spans).toHaveLength(1);
  });
});
