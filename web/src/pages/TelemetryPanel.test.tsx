// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { MonitorApi, MonitorSummary } from "../api/monitor";
import { LocaleProvider } from "../lib/i18n";
import { QueryWrap } from "../test/queryWrapper";
import { TelemetryPanel } from "./TelemetryPanel";

const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(<LocaleProvider>{ui}</LocaleProvider>, { wrapper: QueryWrap });

const emptySummary: MonitorSummary = {
  p95_n_files: null,
  p95_restore_ms: null,
  total_rows_trend: [],
  n_mirror_samples: 0,
  n_restore_samples: 0,
  window_days: null,
};

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
  getSummary: async () => emptySummary,
};

describe("TelemetryPanel", () => {
  // Localized (#465); these assertions are English, so pin the English locale.
  beforeEach(() => localStorage.setItem("ws.locale", "en"));
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("renders in Traditional Chinese under the zh-TW locale (#465)", async () => {
    localStorage.setItem("ws.locale", "zh-TW");
    render(<TelemetryPanel client={withTrace} />);
    expect(await screen.findByText("持久儲存")).toBeInTheDocument();
    expect(screen.queryByText("Durable store")).not.toBeInTheDocument();
  });

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
      getSummary: async () => emptySummary,
    };
    render(<TelemetryPanel client={empty} />);
    expect(await screen.findByText(/No activity yet/i)).toBeInTheDocument();
  });

  it("shows the durable-store summary card (#407)", async () => {
    const client: MonitorApi = {
      getMonitor: async () => [],
      // biome-ignore lint/correctness/useYield: empty feed
      async *streamMonitor() {},
      getSummary: async (): Promise<MonitorSummary> => ({
        p95_n_files: 19,
        p95_restore_ms: 42,
        total_rows_trend: [
          { t: 1000, rows: 5 },
          { t: 2000, rows: 8 },
        ],
        n_mirror_samples: 20,
        n_restore_samples: 20,
        window_days: null,
      }),
    };
    render(<TelemetryPanel client={client} />);
    expect(await screen.findByText("19")).toBeInTheDocument(); // p95 files-per-mirror (awaits query)
    expect(screen.getByText("42 ms")).toBeInTheDocument(); // p95 cold-wake restore
    expect(screen.getByText("8")).toBeInTheDocument(); // latest WorkspaceFile rows
    expect(screen.getByText(/20 mirror/)).toBeInTheDocument(); // sample counts
    expect(screen.getByText("Durable store")).toBeInTheDocument();
  });

  it("renders placeholders when there are no durable-store samples", async () => {
    render(<TelemetryPanel client={withTrace} />); // withTrace uses emptySummary
    expect(await screen.findByText("Durable store")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });
});
