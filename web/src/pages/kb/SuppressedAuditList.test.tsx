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
});
