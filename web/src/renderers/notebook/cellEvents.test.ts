import { describe, expect, it } from "vitest";

import type { CellEvent } from "../../events";
import { reduceCellEvent, startRun, type CellRunState } from "./cellEvents";

function fold(events: CellEvent[]): CellRunState {
  return events.reduce(
    (s, e) => reduceCellEvent(s, e, 1_000_000),
    startRun(0),
  );
}

describe("reduceCellEvent", () => {
  it("coalesces consecutive stdout chunks into one stream output", () => {
    const out = fold([
      { type: "cell_stream", stream: "stdout", text: "Hel" },
      { type: "cell_stream", stream: "stdout", text: "lo\n" },
    ]).outputs;
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({ output_type: "stream", name: "stdout", text: "Hello\n" });
  });

  it("does not coalesce stdout with stderr", () => {
    const out = fold([
      { type: "cell_stream", stream: "stdout", text: "ok\n" },
      { type: "cell_stream", stream: "stderr", text: "warn\n" },
    ]).outputs;
    expect(out).toHaveLength(2);
  });

  it("collects display_data as a separate output", () => {
    const out = fold([
      { type: "cell_display_data", data: { "text/plain": "x" } },
    ]).outputs;
    expect(out).toEqual([
      { output_type: "display_data", data: { "text/plain": "x" } },
    ]);
  });

  it("marks status=error on cell_error and keeps it through cell_done", () => {
    const state = fold([
      { type: "cell_error", ename: "Err", evalue: "boom", traceback: ["..."] },
      { type: "cell_done", execution_count: 3 },
    ]);
    expect(state.status).toBe("error");
    expect(state.execution_count).toBe(3);
  });

  it("marks status=ok and stamps execution_count on cell_done (no error)", () => {
    const state = fold([
      { type: "cell_stream", stream: "stdout", text: "ok\n" },
      { type: "cell_done", execution_count: 7 },
    ]);
    expect(state.status).toBe("ok");
    expect(state.execution_count).toBe(7);
  });

  it("records durationMs from startedAt to done timestamp", () => {
    const start = startRun(0);
    const after = reduceCellEvent(start, { type: "cell_done", execution_count: 1 }, 1234);
    expect(after.durationMs).toBe(1234);
  });
});
