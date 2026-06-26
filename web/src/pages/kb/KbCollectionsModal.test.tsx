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
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
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
    fireEvent.click(screen.getByTestId("kb-collections-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
