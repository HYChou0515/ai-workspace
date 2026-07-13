// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { KbReviewCard } from "../../api/kb";
import type { useReviewInbox } from "../../hooks/useReviewInbox";
import { ReviewDrawer } from "./ReviewDrawer";

afterEach(cleanup);

type Actions = ReturnType<typeof useReviewInbox>;
const mk = () => ({ mutate: vi.fn() });
const actions = () =>
  ({ query: {}, decide: mk(), update: mk(), commit: mk(), answer: mk(), discard: mk() }) as unknown as Actions;

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
    provenance: [{ doc_id: "d", path: "spec.md", snippet: "…RZ3…" }],
    decision: "pending",
  },
  ...over,
});

describe("ReviewDrawer", () => {
  it("saves an edited card body via update.mutate", () => {
    const a = actions();
    render(
      <ReviewDrawer item={{ kind: "card", data: card() }} resolved={false} actions={a} onClose={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText("內容"), { target: { value: "rewritten" } });
    fireEvent.click(screen.getByRole("button", { name: "儲存編輯" }));
    expect(a.update.mutate).toHaveBeenCalledWith({
      runId: "run-aaaa",
      card: expect.objectContaining({ id: "0", body: "rewritten" }),
    });
  });

  it("edits the card's keys (remove one, add one) and saves them via update.mutate", () => {
    const a = actions();
    render(
      <ReviewDrawer item={{ kind: "card", data: card() }} resolved={false} actions={a} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "移除 RZ3" })); // drop the only key
    const add = screen.getByLabelText("新增詞彙");
    fireEvent.change(add, { target: { value: "M4" } });
    fireEvent.keyDown(add, { key: "Enter" }); // commit the new term
    fireEvent.click(screen.getByRole("button", { name: "儲存編輯" }));
    expect(a.update.mutate).toHaveBeenCalledWith({
      runId: "run-aaaa",
      card: expect.objectContaining({ id: "0", keys: ["M4"] }),
    });
  });

  it("shows keys read-only (no add input, no remove buttons) when the user can't act", () => {
    render(
      <ReviewDrawer
        item={{ kind: "card", data: card({ can_act: false }) }}
        resolved={false}
        actions={actions()}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("RZ3")).toBeInTheDocument(); // the key is still shown
    expect(screen.queryByLabelText("新增詞彙")).not.toBeInTheDocument(); // but not editable
    expect(screen.queryByRole("button", { name: "移除 RZ3" })).not.toBeInTheDocument();
  });

  it("does not add a duplicate term", () => {
    const a = actions();
    render(
      <ReviewDrawer item={{ kind: "card", data: card() }} resolved={false} actions={a} onClose={() => {}} />,
    );
    const add = screen.getByLabelText("新增詞彙");
    for (const _ of [0, 1]) {
      fireEvent.change(add, { target: { value: "M4" } });
      fireEvent.keyDown(add, { key: "Enter" });
    }
    fireEvent.click(screen.getByRole("button", { name: "儲存編輯" }));
    expect(a.update.mutate).toHaveBeenCalledWith({
      runId: "run-aaaa",
      card: expect.objectContaining({ keys: ["RZ3", "M4"] }), // M4 added once, not twice
    });
  });

  it("is read-only with a hint and no accept button when the user can't act", () => {
    const a = actions();
    render(
      <ReviewDrawer
        item={{ kind: "card", data: card({ can_act: false }) }}
        resolved={false}
        actions={a}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/只能查看/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "接受" })).not.toBeInTheDocument();
    expect((screen.getByLabelText("內容") as HTMLTextAreaElement).disabled).toBe(true);
  });

  it("shows the card's evidence (provenance)", () => {
    render(
      <ReviewDrawer item={{ kind: "card", data: card() }} resolved={false} actions={actions()} onClose={() => {}} />,
    );
    expect(screen.getByText("spec.md")).toBeInTheDocument();
    expect(screen.getByText("…RZ3…")).toBeInTheDocument();
  });
});
