// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { MonitorApi } from "../api/monitor";
import { QueryWrap } from "../test/queryWrapper";
import { TelemetryPanel } from "./TelemetryPanel";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

const withTrace: MonitorApi = {
  getMonitor: async () => [
    { kind: "trace_start", id: "t1", group_id: "col-9", workflow_name: "Wiki maintainer" },
    {
      kind: "span_end",
      id: "s1",
      trace_id: "t1",
      span_data: { type: "generation", model: "gpt-5.5", usage: { input_tokens: 800, output_tokens: 140 } },
    },
    { kind: "span_end", id: "s2", trace_id: "t1", span_data: { type: "function", name: "write_file" } },
    { kind: "trace_end", id: "t1", group_id: "col-9", workflow_name: "Wiki maintainer" },
  ],
  // biome-ignore lint/correctness/useYield: an empty live feed for the test
  async *streamMonitor() {},
};

describe("TelemetryPanel", () => {
  afterEach(cleanup);

  it("lists a run and expands to reveal its steps (LLM + tool calls)", async () => {
    render(<TelemetryPanel client={withTrace} />);
    const row = await screen.findByRole("button", { name: /Wiki maintainer/ });
    // #171: spans relabeled to "steps" for the diagnostic surface.
    expect(screen.getByText(/2 steps/)).toBeInTheDocument();
    await userEvent.click(row);
    // The maintainer's actual activity: an LLM generation + a write_file tool call.
    expect(await screen.findByText("write_file")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.5")).toBeInTheDocument();
  });

  it("shows an empty state when there's no telemetry yet", async () => {
    const empty: MonitorApi = {
      getMonitor: async () => [],
      // biome-ignore lint/correctness/useYield: empty feed
      async *streamMonitor() {},
    };
    render(<TelemetryPanel client={empty} />);
    expect(await screen.findByText(/No activity yet/i)).toBeInTheDocument();
  });
});
