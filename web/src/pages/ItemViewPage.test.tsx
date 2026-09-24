// @vitest-environment happy-dom
/**
 * The editor-area-only page (#847 Q5.3): what chat mode opens a shown file or
 * layout in. Panes and views, no file tree, no chat.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMarkingStore } from "../hooks/useMarking";
import { viewPageHref } from "../lib/viewPage";
import type { LayoutNode } from "./investigation/paneTree";
import { renderWithQuery } from "../test/queryWrapper";
import { ItemViewPage } from "./ItemViewPage";

vi.mock("../renderers/FileView", () => ({
  FileView: ({ path }: { path: string }) => (
    <div data-testid="file-view" data-marking-store={useMarkingStore() ? "yes" : "no"}>
      {path}
    </div>
  ),
}));
vi.mock("../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));
const useFiles = vi.fn((_id: string) => ({
  kind: "ready",
  items: [{ path: "/v/a.ai.yaml", size: 1 }],
  dirs: [],
  unwalked: [],
  truncated: false,
  refresh: () => {},
}));
vi.mock("../hooks/useInvestigation", () => ({ useFiles: (id: string) => useFiles(id) }));

const layout: LayoutNode = {
  type: "split",
  dir: "row",
  ratio: 0.5,
  a: { type: "leaf", path: "/v/a.ai.yaml" },
  b: {
    type: "split",
    dir: "col",
    ratio: 0.5,
    a: { type: "leaf", path: "/v/b.ai.yaml" },
    b: { type: "leaf", path: "/v/c.csv" },
  },
};

function visit(href: string) {
  return renderWithQuery(
    <MemoryRouter initialEntries={[href]}>
      <Routes>
        <Route path="/a/:slug/:itemId/view" element={<ItemViewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("ItemViewPage", () => {
  it("draws a layout as the workspace's own panes", () => {
    visit(viewPageHref("pm", "PG/1", { layout }));
    expect(screen.getAllByTestId("editor-group")).toHaveLength(3);
    expect(screen.getAllByTestId("file-view").map((v) => v.textContent)).toEqual([
      "/v/a.ai.yaml",
      "/v/b.ai.yaml",
      "/v/c.csv",
    ]);
    // The id in the URL is decoded before it reaches the item's file service.
    expect(useFiles).toHaveBeenCalledWith("PG/1");
  });

  it("draws a single path as one pane", () => {
    visit(viewPageHref("pm", "PG-1", { path: "/v/a.ai.yaml" }));
    expect(screen.getAllByTestId("editor-group")).toHaveLength(1);
    expect(screen.getByTestId("file-view")).toHaveTextContent("/v/a.ai.yaml");
  });

  it("gives its views the item's markings, as the workspace does (#847 P1)", () => {
    visit(viewPageHref("pm", "PG-1", { layout }));
    for (const v of screen.getAllByTestId("file-view")) {
      expect(v).toHaveAttribute("data-marking-store", "yes");
    }
  });

  it("has no file tree and no chat", () => {
    visit(viewPageHref("pm", "PG-1", { layout }));
    expect(screen.queryByTestId("sidebar-pane")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chat-pane")).not.toBeInTheDocument();
  });

  it("says so when the address names nothing it can draw", () => {
    visit("/a/pm/PG-1/view?layout=%7B%7D");
    expect(screen.queryByTestId("editor-group")).not.toBeInTheDocument();
    expect(screen.getByTestId("page-item-view")).toHaveTextContent(/nothing to show/i);
  });
});
