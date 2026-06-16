// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SanityApi, SanityCell, SanityMeta } from "../api/sanity";
import { renderWithQuery } from "../test/queryWrapper";
import { SanityMatrix } from "./SanityMatrix";

const meta: SanityMeta = {
  models: ["ollama_chat/qwen3:14b", "ollama_chat/qwen3:8b"],
  levels: [
    { level: "none", label: "Off" },
    { level: "low", label: "Low" },
    { level: "medium", label: "Medium" },
    { level: "high", label: "High" },
  ],
  questions: [
    {
      key: "q1",
      category: "基礎知識",
      messages: [{ role: "user", content: "台灣的首都是哪裡?" }],
      expected: "回答台北",
      auto_run: true,
      auto_levels: ["none", "medium"],
    },
  ],
};

const cells: SanityCell[] = [
  {
    question_key: "q1",
    level: "none",
    output: "首都是台北市。",
    reasoned: false,
    grade: "pass",
    aux: "",
    error: "",
    latency_ms: 5,
  },
];

function fakeApi(over: Partial<SanityApi> = {}): SanityApi {
  return {
    getMeta: async () => meta,
    getResults: async (model: string) => (model === meta.models[0] ? cells : []),
    run: vi.fn(async () => ({ queued: true })),
    ...over,
  };
}

describe("SanityMatrix", () => {
  afterEach(cleanup);

  it("renders the model picker, level columns, and cells (filled + empty)", async () => {
    renderWithQuery(<SanityMatrix client={fakeApi()} />);

    // question text + expected
    expect(await screen.findByText("台灣的首都是哪裡?")).toBeInTheDocument();
    expect(screen.getByText(/回答台北/)).toBeInTheDocument();
    // all four level headers
    for (const label of ["Off", "Low", "Medium", "High"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // the filled cell shows its output + a ↻ rerun; an empty cell shows ▶ run
    expect(await screen.findByText(/首都是台北市/)).toBeInTheDocument();
    expect(screen.getByTestId("rerun-q1-none")).toBeInTheDocument();
    expect(screen.getByTestId("run-q1-low")).toBeInTheDocument();
  });

  it("runs a single cell with the right payload", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityMatrix client={api} />);
    await userEvent.click(await screen.findByTestId("run-q1-low"));
    await waitFor(() =>
      expect(api.run).toHaveBeenCalledWith({
        model: "ollama_chat/qwen3:14b",
        scope: "cell",
        question_key: "q1",
        level: "low",
      }),
    );
  });

  it("'Run battery' triggers a battery run for the selected model", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityMatrix client={api} />);
    await userEvent.click(await screen.findByTestId("sanity-run-battery"));
    await waitFor(() =>
      expect(api.run).toHaveBeenCalledWith({ model: "ollama_chat/qwen3:14b", scope: "battery" }),
    );
  });

  it("switching the model re-queries that model's cells", async () => {
    const getResults = vi.fn(async (model: string) => (model === meta.models[0] ? cells : []));
    renderWithQuery(<SanityMatrix client={fakeApi({ getResults })} />);
    await screen.findByText("台灣的首都是哪裡?");
    await userEvent.selectOptions(screen.getByTestId("sanity-model"), "ollama_chat/qwen3:8b");
    await waitFor(() => expect(getResults).toHaveBeenCalledWith("ollama_chat/qwen3:8b"));
  });

  it("shows an empty-state when no models are configured", async () => {
    renderWithQuery(<SanityMatrix client={fakeApi({ getMeta: async () => ({ ...meta, models: [] }) })} />);
    expect(await screen.findByTestId("sanity-no-models")).toBeInTheDocument();
  });
});
