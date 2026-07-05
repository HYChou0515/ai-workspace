// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CustomQuestion, SanityApi } from "../api/sanity";
import { LocaleProvider } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { SanityQuestions } from "./SanityQuestions";

const existing: CustomQuestion[] = [
  { id: "c1", category: "自訂", prompt: "舊題目", expected: "舊答案", levels: ["none"], enabled: true },
];

function fakeApi(over: Partial<SanityApi> = {}): SanityApi {
  return {
    getMeta: async () => ({ models: ["m"], levels: [], questions: [] }),
    getResults: async () => [],
    run: vi.fn(async () => ({ queued: true })),
    getVerdicts: async () => [],
    runMissing: vi.fn(async () => ({ count: 0 })),
    rescore: vi.fn(async () => ({ count: 0 })),
    listCustom: async () => existing,
    createCustom: vi.fn(async (b) => ({ id: "new", ...b })),
    updateCustom: vi.fn(async (id, b) => ({ id, ...b })),
    deleteCustom: vi.fn(async () => {}),
    ...over,
  };
}

describe("SanityQuestions (題目管理)", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("localizes its chrome — English under the en locale (#465)", async () => {
    localStorage.setItem("ws.locale", "en");
    renderWithQuery(
      <LocaleProvider>
        <SanityQuestions client={fakeApi()} />
      </LocaleProvider>,
    );
    expect(await screen.findByText("Question manager")).toBeInTheDocument();
    expect(screen.getByTestId("q-save")).toHaveTextContent("Add question");
    expect(screen.queryByText("題目管理")).not.toBeInTheDocument();
  });

  it("lists existing custom questions", async () => {
    renderWithQuery(<SanityQuestions client={fakeApi()} />);
    expect(await screen.findByTestId("custom-row-c1")).toHaveTextContent("舊題目");
  });

  it("authors a new custom question (category + prompt + expected required)", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityQuestions client={api} />);
    await screen.findByTestId("custom-row-c1");

    // save is disabled until the required fields are filled
    expect(screen.getByTestId("q-save")).toBeDisabled();
    await userEvent.type(screen.getByTestId("q-category"), "格式輸出");
    await userEvent.type(screen.getByTestId("q-prompt"), "輸出一個 JSON");
    await userEvent.type(screen.getByTestId("q-expected"), "合法 JSON");
    await userEvent.click(screen.getByTestId("q-save"));

    await waitFor(() =>
      expect(api.createCustom).toHaveBeenCalledWith({
        category: "格式輸出",
        prompt: "輸出一個 JSON",
        expected: "合法 JSON",
        levels: ["none"],
        enabled: true,
      }),
    );
  });

  it("edits an existing question (loads it into the form, then updates)", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityQuestions client={api} />);
    await userEvent.click(await screen.findByTestId("q-edit-c1"));
    // the form is seeded with the question
    expect(screen.getByTestId("q-prompt")).toHaveValue("舊題目");
    await userEvent.click(screen.getByTestId("q-save"));
    await waitFor(() =>
      expect(api.updateCustom).toHaveBeenCalledWith("c1", expect.objectContaining({ prompt: "舊題目" })),
    );
  });

  it("deletes a question", async () => {
    const api = fakeApi();
    renderWithQuery(<SanityQuestions client={api} />);
    await userEvent.click(await screen.findByTestId("q-delete-c1"));
    await waitFor(() => expect(api.deleteCustom).toHaveBeenCalledWith("c1"));
  });

  it("styles the permanent delete as a danger action, not an amber warning (#466)", async () => {
    renderWithQuery(<SanityQuestions client={fakeApi()} />);
    const style = (await screen.findByTestId("q-delete-c1")).getAttribute("style") ?? "";
    // A permanent delete reads in the danger colour, distinct from --warn (which
    // the design system reserves for non-destructive caution).
    expect(style).toMatch(/color:\s*var\(--err\)/);
    expect(style).not.toMatch(/color:\s*var\(--warn\)/);
  });
});
