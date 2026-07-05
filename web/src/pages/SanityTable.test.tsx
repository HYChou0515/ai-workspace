// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SanityApi, SanityCell, SanityMeta } from "../api/sanity";
import { LocaleProvider } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { SanityTable, buildRows, coverageLevels } from "./SanityTable";

const M0 = "ollama_chat/qwen3:14b";
const M1 = "ollama_chat/qwen3:8b";

const meta: SanityMeta = {
  models: [M0, M1],
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
    ai_grade: "pass",
    ai_note: "正確點出台北",
    aux: "",
    error: "",
    latency_ms: 5,
  },
];

function fakeApi(over: Partial<SanityApi> = {}): SanityApi {
  return {
    getMeta: async () => meta,
    getResults: async (m: string) => (m === M0 ? cells : []),
    run: vi.fn(async () => ({ queued: true })),
    getVerdicts: async () => [],
    runMissing: vi.fn(async () => ({ count: 3 })),
    rescore: vi.fn(async () => ({ count: 0 })),
    listCustom: async () => [],
    createCustom: vi.fn(async (b) => ({ id: "x", ...b })),
    updateCustom: vi.fn(async (id, b) => ({ id, ...b })),
    deleteCustom: vi.fn(async () => {}),
    ...over,
  };
}

describe("SanityTable (coverage)", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("localizes its chrome — English under the en locale (#465)", async () => {
    localStorage.setItem("ws.locale", "en");
    renderWithQuery(
      <LocaleProvider>
        <SanityTable client={fakeApi()} />
      </LocaleProvider>,
    );
    expect(await screen.findByTestId(`status-${M0}-q1-none`)).toHaveTextContent("Done");
    expect(screen.getByRole("columnheader", { name: "Question" })).toBeInTheDocument();
    expect(screen.getByTestId("run-missing")).toHaveTextContent("Run all not-run");
    expect(screen.queryByText("完成")).not.toBeInTheDocument();
  });

  it("coverageLevels + buildRows produce the full expected grid with statuses", () => {
    expect(coverageLevels(meta.questions[0])).toEqual(["none", "medium"]);
    const rows = buildRows([M0, M1], meta.questions, { [M0]: cells, [M1]: [] });
    expect(rows).toHaveLength(4); // 2 models × 1 question × 2 levels
    expect(rows.find((r) => r.model === M0 && r.level === "none")?.status).toBe("done");
    expect(rows.find((r) => r.model === M0 && r.level === "medium")?.status).toBe("missing");
    expect(rows.find((r) => r.model === M1 && r.level === "none")?.status).toBe("missing");
  });

  it("renders a row per expected cell — never-run blanks show 未跑 + a 跑 button", async () => {
    renderWithQuery(<SanityTable client={fakeApi()} />);
    // the filled cell is 完成; the blanks are 未跑
    expect(await screen.findByTestId(`status-${M0}-q1-none`)).toHaveTextContent("完成");
    expect(screen.getByTestId(`status-${M0}-q1-medium`)).toHaveTextContent("未跑");
    expect(screen.getByTestId(`status-${M1}-q1-none`)).toHaveTextContent("未跑");
    // a blank offers ▶ 跑; the filled one offers click-to-open
    expect(screen.getByTestId(`run-${M0}-q1-medium`)).toBeInTheDocument();
    expect(screen.getByTestId(`open-${M0}-q1-none`)).toBeInTheDocument();
  });

  it("the coverage summary counts done vs total", async () => {
    renderWithQuery(<SanityTable client={fakeApi()} />);
    expect(await screen.findByTestId("coverage-summary")).toHaveTextContent("已測 1 / 4");
  });

  it("'只看未跑' hides the done rows", async () => {
    renderWithQuery(<SanityTable client={fakeApi()} />);
    await screen.findByTestId(`open-${M0}-q1-none`);
    await userEvent.click(screen.getByTestId("only-missing"));
    await waitFor(() => expect(screen.queryByTestId(`open-${M0}-q1-none`)).not.toBeInTheDocument());
    // the blanks are still there
    expect(screen.getByTestId(`run-${M0}-q1-medium`)).toBeInTheDocument();
  });

  it("'跑掉所有未跑的' calls runMissing for the selected models", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityTable client={api} />);
    await userEvent.click(await screen.findByTestId("run-missing"));
    await waitFor(() =>
      expect(api.runMissing).toHaveBeenCalledWith([M0, M1], null),
    );
  });

  it("a blank cell's ▶ 跑 runs exactly that cell", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityTable client={api} />);
    await userEvent.click(await screen.findByTestId(`run-${M0}-q1-medium`));
    await waitFor(() =>
      expect(api.run).toHaveBeenCalledWith({
        model: M0,
        scope: "cell",
        question_key: "q1",
        level: "medium",
      }),
    );
  });

  it("unchecking a model removes its rows", async () => {
    renderWithQuery(<SanityTable client={fakeApi()} />);
    await screen.findByTestId(`status-${M1}-q1-none`);
    await userEvent.click(screen.getByTestId(`model-toggle-${M1}`));
    await waitFor(() => expect(screen.queryByTestId(`status-${M1}-q1-none`)).not.toBeInTheDocument());
    expect(screen.getByTestId(`status-${M0}-q1-none`)).toBeInTheDocument();
  });

  it("opens a modal with the full output + the AI note", async () => {
    renderWithQuery(<SanityTable client={fakeApi()} />);
    await userEvent.click(await screen.findByTestId(`open-${M0}-q1-none`));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/首都是台北市/)).toBeInTheDocument();
    expect(within(dialog).getByText(/正確點出台北/)).toBeInTheDocument();
  });

  it("shows an empty-state when no models are configured", async () => {
    renderWithQuery(<SanityTable client={fakeApi({ getMeta: async () => ({ ...meta, models: [] }) })} />);
    expect(await screen.findByTestId("sanity-no-models")).toBeInTheDocument();
  });
});
