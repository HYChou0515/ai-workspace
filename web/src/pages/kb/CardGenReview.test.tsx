// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbCardGenCommit, KbContextCard, KbProposedCard } from "../../api/kb";
import { CardGenReview } from "./CardGenReview";

afterEach(cleanup);

const proposal = (over: Partial<KbProposedCard> = {}): KbProposedCard => ({
  keys: ["RZ3"],
  title: "RZ3",
  body: "Auto-drafted from reflow.md.",
  confident: true,
  mode: "new",
  target_card_id: null,
  provenance: [{ doc_id: "d1", path: "reflow.md", snippet: "…passage…" }],
  decision: "pending",
  ...over,
});

/** Stateful host so the controlled proposals update on accept/edit, as the real
 * containers (modal / 待審核 tab) drive it. */
function Harness({
  initial,
  existingCards = [],
  committed = null,
  onCommit = vi.fn(),
  onSave = vi.fn(),
}: {
  initial: KbProposedCard[];
  existingCards?: KbContextCard[];
  committed?: KbCardGenCommit | null;
  onCommit?: () => void;
  onSave?: () => void;
}) {
  const [proposals, setProposals] = useState(initial);
  return (
    <CardGenReview
      proposals={proposals}
      existingCards={existingCards}
      onChange={setProposals}
      onSave={onSave}
      onCommit={onCommit}
      committed={committed}
    />
  );
}

describe("<CardGenReview /> (#175 / #415)", () => {
  it("shows a proposal with its provenance and new/update badge", () => {
    render(<Harness initial={[proposal()]} />);
    const p = screen.getByTestId("cardgen-proposal");
    expect(p).toHaveTextContent("reflow.md"); // provenance "依據"
    expect(p).toHaveTextContent("new");
  });

  it("offers a todo.md bulk view of the proposals", async () => {
    const user = userEvent.setup();
    render(<Harness initial={[proposal()]} />);
    await user.click(screen.getByRole("button", { name: "todo.md" }));
    expect(screen.getByLabelText("todo.md")).toBeInTheDocument();
  });

  it("commits only after a proposal is accepted", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Harness initial={[proposal()]} onCommit={onCommit} />);

    expect(screen.getByRole("button", { name: /套用已接受/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "接受" }));

    const commit = screen.getByRole("button", { name: /套用已接受/ });
    expect(commit).toBeEnabled();
    await user.click(commit);
    expect(onCommit).toHaveBeenCalledOnce();
  });

  it("renders the committed tallies instead of the actions once committed", () => {
    render(<Harness initial={[proposal()]} committed={{ created: 2, updated: 1, skipped: 0 }} />);
    expect(screen.getByTestId("cardgen-committed")).toHaveTextContent("已建立 2");
    expect(screen.queryByRole("button", { name: /套用已接受/ })).not.toBeInTheDocument();
  });

  it("shows an empty state when there are no proposals", () => {
    render(<Harness initial={[]} />);
    expect(screen.getByTestId("cardgen-empty")).toBeInTheDocument();
  });
});
