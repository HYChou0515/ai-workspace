// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { KbSuppressedItem } from "../../api/kb";
import { SuppressedAuditList } from "./SuppressedAuditList";

afterEach(cleanup);

const item = (over: Partial<KbSuppressedItem> = {}): KbSuppressedItem => ({
  collection_id: "c1",
  collection_name: "Alpha",
  kind: "proposal",
  label: "Reflow Zone 3",
  cluster_key: "rz3",
  reason: "near-card",
  ...over,
});

describe("SuppressedAuditList", () => {
  it("lists each auto-dropped candidate with its label and collection", () => {
    render(
      <SuppressedAuditList
        items={[item(), item({ kind: "term_question", label: "Metal 4", reason: "wiki" })]}
      />,
    );
    expect(screen.getByText("Reflow Zone 3")).toBeInTheDocument();
    expect(screen.getByText("Metal 4")).toBeInTheDocument();
    expect(screen.getAllByText("Alpha").length).toBe(2);
  });

  it("explains WHY each candidate was dropped in words, not the raw slug", () => {
    render(
      <SuppressedAuditList
        items={[
          // `wiki` is a TERM-QUESTION verdict only: a card proposal is never graded
          // against the wiki (#537), so kind:"proposal" + reason:"wiki" cannot occur.
          item({ kind: "term_question", reason: "wiki" }),
          item({ reason: "near-card" }),
        ]}
      />,
    );
    expect(screen.queryByText("near-card")).not.toBeInTheDocument();
    expect(screen.getByText(/wiki/i)).toBeInTheDocument();
    expect(screen.getByText(/相近卡片|similar card/i)).toBeInTheDocument();
  });

  it("falls back to the cluster key + a generic reason for a bare item", () => {
    render(
      <SuppressedAuditList items={[item({ label: "", cluster_key: "rz3", reason: "" })]} />,
    );
    expect(screen.getByText("rz3")).toBeInTheDocument();
    expect(screen.getByText(/已被涵蓋|already covered/i)).toBeInTheDocument();
  });

  it("shows an empty state when nothing was suppressed", () => {
    render(<SuppressedAuditList items={[]} />);
    expect(screen.getByTestId("suppressed-empty")).toBeInTheDocument();
  });

  // #506/#577 follow-up: the reader must be able to tell a suppressed CARD from a
  // suppressed QUESTION — otherwise "5 items, reason wiki" reads as "wiki is
  // killing my cards" when in fact those are questions (a card is never
  // wiki-suppressed) and the cards were never drafted.
  it("labels each row's KIND (card proposal vs question) so cards ≠ questions", () => {
    render(
      <SuppressedAuditList
        items={[
          item({ kind: "proposal", label: "Reflow Zone 3", reason: "near-card" }),
          item({ kind: "term_question", label: "Metal 4", reason: "wiki" }),
        ]}
      />,
    );
    expect(screen.getByTestId("suppressed-kind-proposal")).toHaveTextContent(/卡片|card/i);
    expect(screen.getByTestId("suppressed-kind-term_question")).toHaveTextContent(/問題|question/i);
  });

  // #506/#577 follow-up: a near-card suppression names WHICH existing card it
  // duplicated, so a reviewer can verify the dedup instead of trusting a bare
  // "已有相近卡片".
  it("names the existing card a near-card row duplicated", () => {
    render(
      <SuppressedAuditList
        items={[
          item({ kind: "proposal", label: "Reflow Zone 3", reason: "near-card", target_label: "RZ3" }),
        ]}
      />,
    );
    expect(screen.getByText(/RZ3/)).toBeInTheDocument();
  });

  it("summarises the counts by kind at the top", () => {
    render(
      <SuppressedAuditList
        items={[
          item({ kind: "term_question", reason: "wiki" }),
          item({ kind: "term_question", reason: "wiki" }),
          item({ kind: "proposal", reason: "near-card" }),
        ]}
      />,
    );
    const summary = screen.getByTestId("suppressed-summary");
    // two questions + one card — the reader sees at a glance that the wiki-suppressed
    // ones are QUESTIONS, not cards.
    expect(summary).toHaveTextContent(/2/);
    expect(summary).toHaveTextContent(/1/);
  });
});
