/**
 * #534 B — the merge queue's one job is to let a person decide, and the thing
 * they must NOT decide on is the model's own account of itself.
 *
 * Measured against a real local model, roughly half its groupings were wrong,
 * and asking it to justify them only made the wrong ones read better: it merged
 * an inspection machine with a printing machine and explained "a machine used
 * for printing solder paste", which describes one of the two. A reviewer given
 * that sentence and two names approves it.
 *
 * So these tests pin what the row shows: both sides' own documents and the
 * sentences they appeared in, with the model's reason present but marked as the
 * model's.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EntityMergeList } from "./EntityMergeList";

// The entity names are now Links (#534 entity page), so the list needs a router.
const render = (ui: ReactElement) => rtlRender(<MemoryRouter>{ui}</MemoryRouter>);

afterEach(cleanup);

const PROPOSAL = {
  entity_id: "e1",
  other_id: "e2",
  name: "SPI",
  other_name: "錫膏印刷機",
  why: "a machine used for printing solder paste",
  evidence: [
    {
      source_doc_id: "deck-D",
      surface: "SPI",
      text: "Stencil Printer pressure is monitored by SPI",
    },
  ],
  other_evidence: [
    {
      source_doc_id: "deck-C",
      surface: "錫膏印刷機",
      text: "錫膏印刷機(Stencil Printer)壓力不足會造成錫量不足",
    },
  ],
};

describe("EntityMergeList", () => {
  it("shows each side's own sentence, not only the model's summary", () => {
    render(<EntityMergeList proposals={[PROPOSAL]} onAccept={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByText(/monitored by SPI/)).toBeInTheDocument();
    expect(screen.getByText(/壓力不足會造成錫量不足/)).toBeInTheDocument();
  });

  it("attributes the reason to the model rather than stating it as fact", () => {
    render(<EntityMergeList proposals={[PROPOSAL]} onAccept={vi.fn()} onReject={vi.fn()} />);
    const reason = screen.getByTestId("merge-why");
    expect(reason.textContent).toContain("a machine used for printing solder paste");
    // the label marks whose claim it is — the reviewer should weigh it, not obey it
    expect(screen.getByTestId("merge-why-label")).toBeInTheDocument();
  });

  it("names the document each sentence came from so it can be opened", () => {
    render(<EntityMergeList proposals={[PROPOSAL]} onAccept={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByText("deck-C")).toBeInTheDocument();
    expect(screen.getByText("deck-D")).toBeInTheDocument();
  });

  it("passes the pair to the caller when a decision is made", () => {
    const onAccept = vi.fn();
    const onReject = vi.fn();
    render(
      <EntityMergeList proposals={[PROPOSAL]} onAccept={onAccept} onReject={onReject} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "是同一個" }));
    expect(onAccept).toHaveBeenCalledWith("e1", "e2");
    fireEvent.click(screen.getByRole("button", { name: "不是同一個" }));
    expect(onReject).toHaveBeenCalledWith("e1", "e2");
  });

  it("says so plainly when there is nothing to decide", () => {
    render(<EntityMergeList proposals={[]} onAccept={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByTestId("merge-empty")).toBeInTheDocument();
  });

  it("still renders a side that has no readable evidence", () => {
    /** Evidence is filtered by what the reader may open, so a side can arrive
     * empty. The row must still be decidable rather than blank. */
    render(
      <EntityMergeList
        proposals={[{ ...PROPOSAL, other_evidence: [] }]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    );
    expect(screen.getByText("錫膏印刷機")).toBeInTheDocument();
    expect(screen.getByTestId("merge-no-evidence")).toBeInTheDocument();
  });
});

describe("EntityMergeList emphasis", () => {
  it("does not weight one answer over the other", () => {
    /** The interface must not lean toward accepting. Roughly half of a real
     * model's groupings were wrong, so a primary-styled "same thing" would push
     * the reviewer toward the suggestion exactly when they are meant to doubt
     * it. Both answers carry the same weight. */
    render(<EntityMergeList proposals={[PROPOSAL]} onAccept={vi.fn()} onReject={vi.fn()} />);
    const same = screen.getByRole("button", { name: "是同一個" });
    const different = screen.getByRole("button", { name: "不是同一個" });
    expect(same.getAttribute("data-variant")).toBe(different.getAttribute("data-variant"));
  });
});

describe("EntityMergeList sourcing", () => {
  it("opens the document the sentence came from, at that sentence", () => {
    /** A one-line excerpt is enough to reject an obvious mismatch and not enough
     * to settle a close call — the reviewer has to be able to read around it.
     * The link carries the sentence so the document opens with it highlighted,
     * and opens in a new tab so their place in the queue survives. */
    render(<EntityMergeList proposals={[PROPOSAL]} onAccept={vi.fn()} onReject={vi.fn()} />);
    const link = screen.getByRole("link", { name: "deck-D" });
    expect(link.getAttribute("href")).toContain(encodeURIComponent("deck-D"));
    expect(link.getAttribute("href")).toContain("hl=");
    expect(link).toHaveAttribute("target", "_blank");
  });
});

describe("EntityMergeList framing", () => {
  it("states the question in words rather than a symbol", () => {
    /** The divider used to be "≟", a glyph rare enough that fonts substitute it
     * at another size — it arrived tiny and read as a smudge. A sentence needs no
     * font support and no legend. */
    render(<EntityMergeList proposals={[PROPOSAL]} onAccept={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByText("這兩個是同一個東西嗎?")).toBeInTheDocument();
  });
});

describe("EntityMergeList kinds", () => {
  const TYPED = {
    ...PROPOSAL,
    kind: "機台",
    other_kind: "缺陷",
    collection_ids: ["c1"],
  };

  it("shows what kind each side is", () => {
    /** Whoever knows the machines is not whoever knows the defects, so the row
     * has to say which it is before a reviewer can tell whether it is theirs. */
    render(<EntityMergeList proposals={[TYPED]} onAccept={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByText("機台")).toBeInTheDocument();
    expect(screen.getByText("缺陷")).toBeInTheDocument();
  });

  it("marks a pair whose two sides are different kinds", () => {
    /** The model grouping a machine with a defect is the shape its worst mistakes
     * take — it merged an inspection machine with a printing machine, and a
     * defect with the joint it occurs on. A row that disagrees with itself about
     * what kind of thing this is deserves a second look before anything else. */
    render(<EntityMergeList proposals={[TYPED]} onAccept={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByTestId("merge-kind-mismatch")).toBeInTheDocument();
  });

  it("does not mark a pair whose sides agree", () => {
    render(
      <EntityMergeList
        proposals={[{ ...TYPED, other_kind: "機台" }]}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("merge-kind-mismatch")).toBeNull();
  });
});
