// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { renderWithQuery } from "../../test/queryWrapper";
import { WikiCorrectionDialog } from "./WikiCorrectionDialog";

afterEach(cleanup);

function client(over: Partial<KbApi> = {}): KbApi {
  return { ...mockKbApi, ...over };
}

const base = {
  collectionId: "c1",
  question: "When was Foo founded?",
  answer: "Foo was founded in 1989.",
  wikiPages: ["/entities/foo.md"],
  onClose: () => {},
};

describe("WikiCorrectionDialog (#397)", () => {
  it("one-click AI draft prefills the instruction from a draft result", async () => {
    const draftWikiCorrection = vi.fn(async () => ({
      action: "draft" as const,
      instruction: "Founded in 1998, not 1989.",
      target_page: "/entities/foo.md",
      questions: [],
    }));
    renderWithQuery(<WikiCorrectionDialog {...base} client={client({ draftWikiCorrection })} />);

    await userEvent.click(screen.getByRole("button", { name: /AI 幫我草擬/ }));
    expect(draftWikiCorrection).toHaveBeenCalledWith("c1", {
      question: base.question,
      answer: base.answer,
      wiki_pages: ["/entities/foo.md"],
      answered: [],
    });
    const box = await screen.findByDisplayValue("Founded in 1998, not 1989.");
    expect(box).toBeInTheDocument();
    expect(screen.getByDisplayValue("/entities/foo.md")).toBeInTheDocument();
  });

  it("an 'ask' result shows clarifying questions, then folds answers into the next draft", async () => {
    const draftWikiCorrection = vi
      .fn()
      .mockResolvedValueOnce({
        action: "ask",
        instruction: "",
        target_page: "",
        questions: ["Which date is wrong?"],
      })
      .mockResolvedValueOnce({
        action: "draft",
        instruction: "Use 1998.",
        target_page: "",
        questions: [],
      });
    renderWithQuery(<WikiCorrectionDialog {...base} client={client({ draftWikiCorrection })} />);

    await userEvent.click(screen.getByRole("button", { name: /AI 幫我草擬/ }));
    const qInput = await screen.findByLabelText("Which date is wrong?");
    await userEvent.type(qInput, "the founding year");
    await userEvent.click(screen.getByRole("button", { name: /AI 幫我草擬/ }));

    // second call folds the answered question in
    expect(draftWikiCorrection).toHaveBeenLastCalledWith("c1", {
      question: base.question,
      answer: base.answer,
      wiki_pages: ["/entities/foo.md"],
      answered: [{ question: "Which date is wrong?", answer: "the founding year" }],
    });
    expect(await screen.findByDisplayValue("Use 1998.")).toBeInTheDocument();
  });

  it("submit sends the edited instruction + target and shows a confirmation", async () => {
    const submitWikiCorrection = vi.fn(async () => ({ path: "/corrections/entities-foo.md" }));
    renderWithQuery(<WikiCorrectionDialog {...base} client={client({ submitWikiCorrection })} />);

    await userEvent.type(
      screen.getByPlaceholderText(/Foo 成立於/),
      "Founded 1998.",
    );
    await userEvent.type(screen.getByPlaceholderText(/entities\/foo\.md/), "/entities/foo.md");
    await userEvent.click(screen.getByRole("button", { name: /送出修正/ }));

    expect(submitWikiCorrection).toHaveBeenCalledWith("c1", {
      instruction: "Founded 1998.",
      target_page: "/entities/foo.md",
    });
    expect(await screen.findByText(/wiki 更新中/)).toBeInTheDocument();
  });

  it("submit is disabled until the instruction is non-empty", async () => {
    const submitWikiCorrection = vi.fn();
    renderWithQuery(<WikiCorrectionDialog {...base} client={client({ submitWikiCorrection })} />);
    expect(screen.getByRole("button", { name: /送出修正/ })).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/Foo 成立於/), "x");
    expect(screen.getByRole("button", { name: /送出修正/ })).toBeEnabled();
  });
});
