// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { KbReviewCard, KbReviewCluster, KbReviewQuestion } from "../../api/kb";
import { ClusterReviewList } from "./ClusterReviewList";

afterEach(cleanup);

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
});
