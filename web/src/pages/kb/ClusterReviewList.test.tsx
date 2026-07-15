// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { KbReviewCard, KbReviewCluster, KbReviewQuestion } from "../../api/kb";
import type { useReviewInbox } from "../../hooks/useReviewInbox";
import { ClusterReviewList } from "./ClusterReviewList";

afterEach(cleanup);

type Actions = ReturnType<typeof useReviewInbox>;

/** A minimal fake of the review-inbox mutations — only `.mutate` is exercised. */
function fakeActions(over: Record<string, unknown> = {}): Actions {
  const stub = () => ({ mutate: vi.fn() });
  return {
    query: {},
    decide: stub(),
    update: stub(),
    commit: stub(),
    answer: stub(),
    discard: stub(),
    ...over,
  } as unknown as Actions;
}

const card = (title: string, keys: string[] = [title]): KbReviewCard => ({
  run_id: "r1",
  collection_id: "c1",
  collection_name: "Alpha",
  can_act: true,
  created_time: 0,
  card: {
    id: "0",
    keys,
    title,
    body: "",
    confident: true,
    mode: "new",
    target_card_id: null,
    provenance: [],
    decision: "pending",
  },
});

const question = (term: string): KbReviewQuestion => ({
  collection_id: "c1",
  collection_name: "Alpha",
  can_act: true,
  created_time: 0,
  question: {
    id: "q1",
    collection_id: "c1",
    kind: "term",
    status: "open",
    question_text: `What is ${term}?`,
    term,
    source_doc_ids: [],
    source_doc_id: "",
    quote: "",
  },
});

const cluster = (over: Partial<KbReviewCluster> = {}): KbReviewCluster => ({
  cluster_key: "rz3",
  collection_id: "c1",
  collection_name: "Alpha",
  can_act: true,
  created_time: 0,
  cards: [],
  questions: [],
  size: 0,
  ...over,
});

describe("ClusterReviewList", () => {
  it("renders one row per concept with its member count", () => {
    const clusters = [
      cluster({ cluster_key: "rz3", cards: [card("Reflow Zone 3")], questions: [question("RZ3 timing")], size: 2 }),
      cluster({ cluster_key: "m4", cards: [card("Metal 4")], size: 1 }),
    ];
    render(<ClusterReviewList clusters={clusters} />);

    const rows = screen.getAllByRole("button", { name: /concept|概念|項|item/i });
    expect(rows.length).toBe(2);
    // the two-member cluster surfaces its count
    expect(screen.getByText("Reflow Zone 3")).toBeInTheDocument();
    expect(screen.getByText(/2/)).toBeInTheDocument();
  });

  it("expands a cluster to reveal its member proposals and questions", () => {
    const clusters = [
      cluster({
        cluster_key: "rz3",
        cards: [card("Reflow Zone 3")],
        questions: [question("RZ3 timing")],
        size: 2,
      }),
    ];
    render(<ClusterReviewList clusters={clusters} />);

    // members hidden until expanded
    expect(screen.queryByText("RZ3 timing")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Reflow Zone 3|concept|概念/i }));
    expect(screen.getByText("RZ3 timing")).toBeInTheDocument();
  });

  it("shows an empty state when there are no clusters", () => {
    render(<ClusterReviewList clusters={[]} />);
    expect(screen.getByTestId("cluster-empty")).toBeInTheDocument();
  });

  it("accepts a card member inline without leaving the grouped view", () => {
    const decide = { mutate: vi.fn() };
    const actions = fakeActions({ decide });
    render(
      <ClusterReviewList
        clusters={[cluster({ cards: [card("Reflow Zone 3")], size: 1 })]}
        actions={actions}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Reflow Zone 3|concept/i })); // expand
    fireEvent.click(screen.getByRole("button", { name: /accept|接受/i }));
    expect(decide.mutate).toHaveBeenCalledWith({
      runId: "r1",
      cardId: "0",
      decision: "accepted",
    });
  });

  it("rejects a card member inline", () => {
    const decide = { mutate: vi.fn() };
    const actions = fakeActions({ decide });
    render(
      <ClusterReviewList
        clusters={[cluster({ cards: [card("Reflow Zone 3")], size: 1 })]}
        actions={actions}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Reflow Zone 3|concept/i }));
    fireEvent.click(screen.getByRole("button", { name: /reject|拒絕/i }));
    expect(decide.mutate).toHaveBeenCalledWith({
      runId: "r1",
      cardId: "0",
      decision: "rejected",
    });
  });

  it("opens the answer drawer for a question member", () => {
    const actions = fakeActions();
    render(
      <ClusterReviewList
        clusters={[cluster({ questions: [question("RZ3 timing")], size: 1 })]}
        actions={actions}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /concept|RZ3 timing/i })); // expand
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /answer|回答/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("hides inline actions on read-only members", () => {
    const actions = fakeActions();
    render(
      <ClusterReviewList
        clusters={[cluster({ cards: [{ ...card("Reflow Zone 3"), can_act: false }], size: 1 })]}
        actions={actions}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Reflow Zone 3|concept/i }));
    expect(screen.queryByRole("button", { name: /accept|接受/i })).not.toBeInTheDocument();
  });

  it("applies a whole cluster's accepted cards in one click", () => {
    const commit = { mutate: vi.fn() };
    const actions = fakeActions({ commit });
    const accepted: KbReviewCard = {
      ...card("Reflow Zone 3"),
      card: { ...card("Reflow Zone 3").card, decision: "accepted" },
    };
    render(
      <ClusterReviewList clusters={[cluster({ cards: [accepted], size: 1 })]} actions={actions} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /apply|套用/i }));
    expect(commit.mutate).toHaveBeenCalledWith([{ run_id: "r1", card_id: "0" }]);
  });

  it("shows no apply-cluster button until a card is accepted", () => {
    const actions = fakeActions();
    render(
      <ClusterReviewList
        clusters={[cluster({ cards: [card("Reflow Zone 3")], size: 1 })]}
        actions={actions}
      />,
    );
    expect(screen.queryByRole("button", { name: /apply|套用/i })).not.toBeInTheDocument();
  });

  it("opens the editable drawer when a card member is clicked", () => {
    const actions = fakeActions();
    render(
      <ClusterReviewList
        clusters={[cluster({ cards: [card("Reflow Zone 3")], size: 1 })]}
        actions={actions}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /concept|Reflow Zone 3/i })); // expand
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reflow Zone 3" })); // the member label
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
