// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbCollection } from "../../api/kb";
import { KbCollectionsModal } from "./KbCollectionsModal";

afterEach(cleanup);

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  is_global: false,
  ...over,
});

const COLLECTIONS = [
  coll({ resource_id: "a", name: "Alpha" }),
  coll({ resource_id: "b", name: "Beta" }),
];

describe("KbCollectionsModal", () => {
  it("renders the shared checklist over the given collections", () => {
    render(
      <KbCollectionsModal
        collections={COLLECTIONS}
        selected={new Set(["a"])}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("collection-check-a")).toBeChecked();
    expect(screen.getByTestId("collection-check-b")).not.toBeChecked();
  });

  it("applies a checkbox toggle live (no save step)", () => {
    const onChange = vi.fn();
    render(
      <KbCollectionsModal
        collections={COLLECTIONS}
        selected={new Set(["a"])}
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("collection-check-b"));
    expect(onChange).toHaveBeenCalledWith(new Set(["a", "b"]));
  });

  it("closes on the Done button", () => {
    const onClose = vi.fn();
    render(
      <KbCollectionsModal
        collections={COLLECTIONS}
        selected={new Set()}
        onChange={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("kb-collections-done"));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders a Global badge on a global collection and reflects it as checked", () => {
    const collections = [coll({ resource_id: "g", name: "Baseline", is_global: true }), ...COLLECTIONS];
    render(
      <KbCollectionsModal
        collections={collections}
        // A global starts in scope (checked); the KB chat seeds it this way.
        selected={new Set(["g"])}
        onChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("collection-global-badge-g")).toBeInTheDocument();
    expect(screen.getByTestId("collection-check-g")).toBeChecked();
  });

  it("un-checking a global drops it from the selection (→ excluded upstream)", () => {
    const onChange = vi.fn();
    const collections = [coll({ resource_id: "g", name: "Baseline", is_global: true }), ...COLLECTIONS];
    render(
      <KbCollectionsModal
        collections={collections}
        selected={new Set(["g", "a"])}
        onChange={onChange}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("collection-check-g"));
    // Removed from the checked set — the panel maps a NOT-checked global to
    // excluded_collection_ids on create.
    expect(onChange).toHaveBeenCalledWith(new Set(["a"]));
  });

  it("closes on a backdrop click but not on a click inside the dialog", () => {
    const onClose = vi.fn();
    render(
      <KbCollectionsModal
        collections={COLLECTIONS}
        selected={new Set()}
        onChange={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("kb-collections-dialog"));
    expect(onClose).not.toHaveBeenCalled();
    // ModalShell derives the backdrop testid from the panel's (`${id}-backdrop`).
    fireEvent.click(screen.getByTestId("kb-collections-dialog-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
