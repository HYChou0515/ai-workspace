// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbCollection } from "../api/kb";
import { CollectionsChecklist } from "./CollectionsChecklist";

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
  coll({ resource_id: "a", name: "Alpha", doc_count: 3 }),
  coll({ resource_id: "b", name: "Beta", doc_count: 7 }),
  coll({ resource_id: "c", name: "Gamma", doc_count: 0 }),
];

describe("CollectionsChecklist", () => {
  it("renders a checkbox row per collection, reflecting the selected set", () => {
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set(["b"])} onChange={vi.fn()} />,
    );
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText(/7/)).toBeInTheDocument(); // Beta doc_count
    expect(screen.getByTestId("collection-check-b")).toBeChecked();
    expect(screen.getByTestId("collection-check-a")).not.toBeChecked();
  });

  it("emits onChange with the id added when an unchecked row is clicked", () => {
    const onChange = vi.fn();
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set(["b"])} onChange={onChange} />,
    );
    fireEvent.click(screen.getByTestId("collection-check-a"));
    expect(onChange).toHaveBeenCalledWith(new Set(["b", "a"]));
  });

  it("emits onChange with the id removed when a checked row is clicked", () => {
    const onChange = vi.fn();
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set(["a", "b"])} onChange={onChange} />,
    );
    fireEvent.click(screen.getByTestId("collection-check-b"));
    expect(onChange).toHaveBeenCalledWith(new Set(["a"]));
  });

  it("filters rows by the search box (case-insensitive)", () => {
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set()} onChange={vi.fn()} />,
    );
    fireEvent.change(screen.getByTestId("collections-search"), { target: { value: "BET" } });
    expect(screen.getByTestId("collection-row-b")).toBeInTheDocument();
    expect(screen.queryByTestId("collection-row-a")).not.toBeInTheDocument();
  });

  it("select-all adds every visible (filtered) collection to the selection", () => {
    const onChange = vi.fn();
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set()} onChange={onChange} />,
    );
    fireEvent.click(screen.getByTestId("collections-select-all"));
    expect(onChange).toHaveBeenCalledWith(new Set(["a", "b", "c"]));
  });

  it("select-all under an active filter only adds the matches (union with existing)", () => {
    const onChange = vi.fn();
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set(["a"])} onChange={onChange} />,
    );
    fireEvent.change(screen.getByTestId("collections-search"), { target: { value: "bet" } });
    fireEvent.click(screen.getByTestId("collections-select-all"));
    expect(onChange).toHaveBeenCalledWith(new Set(["a", "b"]));
  });

  it("clear removes every visible (filtered) collection from the selection", () => {
    const onChange = vi.fn();
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set(["a", "b", "c"])} onChange={onChange} />,
    );
    fireEvent.change(screen.getByTestId("collections-search"), { target: { value: "bet" } });
    fireEvent.click(screen.getByTestId("collections-clear"));
    expect(onChange).toHaveBeenCalledWith(new Set(["a", "c"]));
  });

  it("shows a no-match hint when the search matches nothing", () => {
    render(
      <CollectionsChecklist collections={COLLECTIONS} selected={new Set()} onChange={vi.fn()} />,
    );
    fireEvent.change(screen.getByTestId("collections-search"), { target: { value: "zzz" } });
    expect(screen.getByText(/zzz/)).toBeInTheDocument();
    expect(screen.queryByTestId("collection-row-a")).not.toBeInTheDocument();
  });

  it("shows an empty hint and no select-all bar when there are no collections", () => {
    render(<CollectionsChecklist collections={[]} selected={new Set()} onChange={vi.fn()} />);
    expect(screen.getByText("目前沒有任何知識庫可選。")).toBeInTheDocument();
    expect(screen.queryByTestId("collections-select-all")).not.toBeInTheDocument();
  });

  it("renders a Global badge only on global collections", () => {
    render(
      <CollectionsChecklist
        collections={[coll({ resource_id: "g", name: "Baseline", is_global: true }), ...COLLECTIONS]}
        selected={new Set()}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("collection-global-badge-g")).toBeInTheDocument();
    expect(screen.queryByTestId("collection-global-badge-a")).not.toBeInTheDocument();
  });
});
