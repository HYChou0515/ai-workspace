// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
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

  it("clicking a filled cell's output opens a modal with the full, untruncated output", async () => {
    const long = "甲".repeat(200);
    const api = fakeApi({
      getResults: async (model: string) =>
        model === meta.models[0] ? [{ ...cells[0], output: long }] : [],
    });
    renderWithQuery(<SanityMatrix client={api} />);

    // the in-cell preview is truncated (120 chars + …), so the full string is NOT in the grid
    const preview = await screen.findByTestId("output-q1-none");
    expect(preview).not.toHaveTextContent(long);

    await userEvent.click(preview);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(long)).toBeInTheDocument();
  });

  it("the output modal shows the level label and question prompt as context", async () => {
    renderWithQuery(<SanityMatrix client={fakeApi()} />);
    await userEvent.click(await screen.findByTestId("output-q1-none"));
    const dialog = await screen.findByRole("dialog");
    // the column's level label + the question prompt, so you know which cell this is
    expect(within(dialog).getByText(/Off/)).toBeInTheDocument();
    expect(within(dialog).getByText(/台灣的首都是哪裡/)).toBeInTheDocument();
  });

  it("the output modal footer surfaces grade, reasoning and latency", async () => {
    renderWithQuery(<SanityMatrix client={fakeApi()} />);
    await userEvent.click(await screen.findByTestId("output-q1-none"));
    const dialog = await screen.findByRole("dialog");
    // latency is shown nowhere else in the grid; the dot/aux are restated for context
    expect(within(dialog).getByText(/5\s*ms/)).toBeInTheDocument();
    expect(within(dialog).getByText(/pass/)).toBeInTheDocument();
    expect(within(dialog).getByText(/no reasoning/)).toBeInTheDocument();
  });

  it("closes the output modal via the button, the backdrop, and Escape", async () => {
    renderWithQuery(<SanityMatrix client={fakeApi()} />);

    // 1) close button
    await userEvent.click(await screen.findByTestId("output-q1-none"));
    await screen.findByRole("dialog");
    await userEvent.click(screen.getByTestId("sanity-output-close"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // 2) Escape
    await userEvent.click(screen.getByTestId("output-q1-none"));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // 3) backdrop
    await userEvent.click(screen.getByTestId("output-q1-none"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(dialog.parentElement as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("an errored cell opens the modal showing the full error text", async () => {
    const longError = "RuntimeError: " + "x".repeat(200);
    const api = fakeApi({
      getResults: async (model: string) =>
        model === meta.models[0]
          ? [{ ...cells[0], output: "", grade: "", error: longError }]
          : [],
    });
    renderWithQuery(<SanityMatrix client={api} />);
    await userEvent.click(await screen.findByTestId("output-q1-none"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(longError)).toBeInTheDocument();
  });

  it("shows an empty-state when no models are configured", async () => {
    renderWithQuery(<SanityMatrix client={fakeApi({ getMeta: async () => ({ ...meta, models: [] }) })} />);
    expect(await screen.findByTestId("sanity-no-models")).toBeInTheDocument();
  });
});
