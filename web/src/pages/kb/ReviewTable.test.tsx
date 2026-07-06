// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

afterEach(cleanup);

import type { KbReviewCard, KbReviewQuestion } from "../../api/kb";
import type { useReviewInbox } from "../../hooks/useReviewInbox";
import { ReviewTable } from "./ReviewTable";

type Actions = ReturnType<typeof useReviewInbox>;
const mk = () => ({ mutate: vi.fn() });
function fakeActions() {
  return {
    query: {},
    decide: mk(),
    update: mk(),
    commit: mk(),
    answer: mk(),
    discard: mk(),
  } as unknown as Actions;
}

const card = (over: Partial<KbReviewCard> = {}): KbReviewCard => ({
  run_id: "run-aaaa",
  collection_id: "c1",
  collection_name: "Alpha",
  can_act: true,
  created_time: 0,
  card: {
    id: "0",
    keys: ["RZ3"],
    title: "Reflow Zone 3",
    body: "the third zone",
    confident: true,
    mode: "new",
    target_card_id: null,
    provenance: [],
    decision: "pending",
  },
  ...over,
});

const question = (over: Partial<KbReviewQuestion> = {}): KbReviewQuestion => ({
  collection_id: "c1",
  collection_name: "Alpha",
  can_act: true,
  created_time: 0,
  question: {
    id: "q1",
    collection_id: "c1",
    kind: "term",
    status: "open",
    question_text: "What is R7?",
    term: "R7",
    source_doc_ids: ["d1"],
    source_doc_id: "",
    quote: "",
  },
  ...over,
});

describe("ReviewTable", () => {
  it("renders a card row and a question row with their collection", () => {
    render(
      <ReviewTable cards={[card()]} questions={[question()]} resolved={false} actions={fakeActions()} />,
    );
    expect(screen.getByText("Reflow Zone 3")).toBeInTheDocument();
    expect(screen.getByText("R7")).toBeInTheDocument();
    expect(screen.getAllByText("Alpha").length).toBeGreaterThanOrEqual(2);
  });

  it("hides read-only (non-actionable) rows when 'only actionable' is checked", () => {
    render(
      <ReviewTable
        cards={[card(), card({ collection_name: "Locked", can_act: false, card: { ...card().card, id: "1", title: "Hidden" } })]}
        questions={[]}
        resolved={false}
        actions={fakeActions()}
      />,
    );
    expect(screen.getByText("Hidden")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("只看我能操作的"));
    expect(screen.queryByText("Hidden")).not.toBeInTheDocument();
    expect(screen.getByText("Reflow Zone 3")).toBeInTheDocument();
  });

  it("shows a read-only marker and no accept button for a non-actionable card", () => {
    render(
      <ReviewTable
        cards={[card({ can_act: false })]}
        questions={[]}
        resolved={false}
        actions={fakeActions()}
      />,
    );
    expect(screen.getByText("無編輯權")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "接受" })).not.toBeInTheDocument();
  });

  it("accepts a card inline, calling decide with the card's ids", () => {
    const actions = fakeActions();
    render(<ReviewTable cards={[card()]} questions={[]} resolved={false} actions={actions} />);
    fireEvent.click(screen.getByRole("button", { name: "接受" }));
    expect(actions.decide.mutate).toHaveBeenCalledWith({
      runId: "run-aaaa",
      cardId: "0",
      decision: "accepted",
    });
  });

  it("selecting rows and applying commits the selected refs", () => {
    const actions = fakeActions();
    render(<ReviewTable cards={[card()]} questions={[]} resolved={false} actions={actions} />);
    fireEvent.click(screen.getByLabelText("選取"));
    fireEvent.click(screen.getByRole("button", { name: /套用選取/ }));
    expect(actions.commit.mutate).toHaveBeenCalledWith([{ run_id: "run-aaaa", card_id: "0" }]);
  });

  it("filters rows by the search box", () => {
    render(
      <ReviewTable
        cards={[card(), card({ card: { ...card().card, id: "1", title: "Metal Four", keys: ["M4"] } })]}
        questions={[]}
        resolved={false}
        actions={fakeActions()}
      />,
    );
    fireEvent.change(screen.getByLabelText("搜尋標題、詞彙、問題…"), { target: { value: "metal" } });
    expect(screen.getByText("Metal Four")).toBeInTheDocument();
    expect(screen.queryByText("Reflow Zone 3")).not.toBeInTheDocument();
  });

  it("opens the drawer on a question row and answers it", () => {
    const actions = fakeActions();
    render(<ReviewTable cards={[]} questions={[question()]} resolved={false} actions={actions} />);
    fireEvent.click(screen.getByRole("button", { name: "回答" })); // opens the drawer
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("送出"), { target: { value: "a reflow recipe" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "送出" }));
    expect(actions.answer.mutate).toHaveBeenCalledWith({ id: "q1", answer: "a reflow recipe" });
  });
});
