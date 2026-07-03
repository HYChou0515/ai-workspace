// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { KbDocQuestion } from "../api/kb";
import { _resetKbMock, mockKbApi } from "../api/kbMock";
import { renderWithQuery } from "../test/queryWrapper";
import { DocQuestionsPage } from "./DocQuestionsPage";

const term = (over: Partial<KbDocQuestion> = {}): KbDocQuestion => ({
  id: "q-term",
  collection_id: "col-1",
  kind: "term",
  status: "open",
  question_text: "「M4」在你們的語境是指什麼？",
  term: "M4",
  source_doc_ids: ["d1", "d2"],
  source_doc_id: "d1",
  quote: "",
  ...over,
});

const passage = (over: Partial<KbDocQuestion> = {}): KbDocQuestion => ({
  id: "q-desc",
  collection_id: "col-1",
  kind: "description",
  status: "open",
  question_text: "這段的『回流』步驟順序是什麼？",
  term: "",
  source_doc_ids: [],
  source_doc_id: "d9",
  quote: "reflow 後先量測再修正",
  ...over,
});

/** A client whose inbox returns a fixed list, but whose write methods still
 * delegate to the mock so spies observe the real signatures. */
const clientWith = (items: KbDocQuestion[]) =>
  ({ ...mockKbApi, getDocQuestions: () => Promise.resolve(items) }) as typeof mockKbApi;

describe("DocQuestionsPage (#377)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a loading placeholder before the inbox resolves — not the empty copy", () => {
    const client = {
      ...mockKbApi,
      getDocQuestions: () => new Promise<KbDocQuestion[]>(() => {}),
    } as typeof mockKbApi;
    renderWithQuery(<DocQuestionsPage client={client} />);
    expect(screen.getByTestId("review-loading")).toBeInTheDocument();
    expect(screen.queryByText(/沒有待釐清/)).not.toBeInTheDocument();
  });

  it("shows the empty copy once the inbox resolves with no open questions", async () => {
    renderWithQuery(<DocQuestionsPage client={clientWith([])} />);
    expect(await screen.findByText(/沒有待釐清/)).toBeInTheDocument();
    expect(screen.queryByTestId("review-loading")).not.toBeInTheDocument();
  });

  it("lists a term question with its term and how many documents raised it", async () => {
    renderWithQuery(<DocQuestionsPage client={clientWith([term()])} />);
    expect(await screen.findByText("「M4」在你們的語境是指什麼？")).toBeInTheDocument();
    expect(screen.getByText("M4")).toBeInTheDocument();
    expect(screen.getByText(/2 份文件提到/)).toBeInTheDocument();
  });

  it("shows the quoted passage for a description question", async () => {
    renderWithQuery(<DocQuestionsPage client={clientWith([passage()])} />);
    expect(await screen.findByText("這段的『回流』步驟順序是什麼？")).toBeInTheDocument();
    expect(screen.getByText("reflow 後先量測再修正")).toBeInTheDocument();
  });

  it("keeps submit disabled until an answer is typed", async () => {
    renderWithQuery(<DocQuestionsPage client={clientWith([term()])} />);
    await screen.findByText("「M4」在你們的語境是指什麼？");
    const submit = screen.getByRole("button", { name: "送出" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "Metal 4 金屬層");
    expect(submit).toBeEnabled();
  });

  it("answers a question through answerDocQuestion with the typed text", async () => {
    const spy = vi.spyOn(mockKbApi, "answerDocQuestion");
    renderWithQuery(<DocQuestionsPage client={clientWith([term()])} />);
    await screen.findByText("「M4」在你們的語境是指什麼？");

    await userEvent.type(screen.getByRole("textbox"), "Metal 4 金屬層");
    await userEvent.click(screen.getByRole("button", { name: "送出" }));

    expect(spy).toHaveBeenCalledWith("q-term", "Metal 4 金屬層");
  });

  it("discards a question through discardDocQuestion", async () => {
    const spy = vi.spyOn(mockKbApi, "discardDocQuestion");
    renderWithQuery(<DocQuestionsPage client={clientWith([term()])} />);
    await screen.findByText("「M4」在你們的語境是指什麼？");

    await userEvent.click(screen.getByRole("button", { name: "丟棄" }));

    expect(spy).toHaveBeenCalledWith("q-term");
  });
});
