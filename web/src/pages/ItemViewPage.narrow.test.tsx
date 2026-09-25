// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P13 — a layout is usable in narrow panes.
 *
 * Measured in Chromium (five chart panes): at 1440 wide with the file tree and
 * chat open, a pane was 128 px and its tab strip — a 132 px tab plus "Edit" and
 * split buttons — painted its tab 5 px into the chat column; at 390 wide the
 * `/view` page scrolled sideways (445 px of content in 390). A pane now clips
 * what it cannot fit, a tab's title truncates inside its tab, and a narrow
 * pane's Edit toggle is its icon alone. happy-dom lays nothing out, so this
 * holds the styles and the width that flips them; the overlaps are measured in
 * a browser.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { viewPageHref } from "../lib/viewPage";
import { renderWithQuery } from "../test/queryWrapper";
import { NARROW_TAB_STRIP } from "./investigation/WorkspaceShell";
import { ItemViewPage } from "./ItemViewPage";

vi.mock("../renderers/FileView", () => ({
  FileView: ({ path }: { path: string }) => <div data-testid="file-view">{path}</div>,
}));
vi.mock("../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));
vi.mock("../hooks/useInvestigation", () => ({
  useFiles: () => ({
    kind: "ready",
    items: [{ path: "/views/a-view-with-a-long-name.ai.yaml", size: 1 }],
    dirs: [],
    unwalked: [],
    truncated: false,
    refresh: () => {},
  }),
}));

const realRO = globalThis.ResizeObserver;
let emit: (width: number) => void = () => {};
beforeEach(() => {
  const callbacks: ResizeObserverCallback[] = [];
  globalThis.ResizeObserver = class {
    constructor(cb: ResizeObserverCallback) {
      callbacks.push(cb);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  emit = (width) =>
    act(() => {
      for (const cb of callbacks) cb([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver);
    });
});
afterEach(() => {
  globalThis.ResizeObserver = realRO;
  cleanup();
});

function visit() {
  return renderWithQuery(
    <MemoryRouter initialEntries={[viewPageHref("pm", "PG-1", { path: "/views/a-view-with-a-long-name.ai.yaml" })]}>
      <Routes>
        <Route path="/a/:slug/:itemId/view" element={<ItemViewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("a pane of a layout", () => {
  it("clips what it cannot fit, so nothing paints outside its column", () => {
    visit();
    expect(screen.getByTestId("editor-group").style.overflow).toBe("hidden");
  });

  it("truncates a tab's title inside its tab", () => {
    visit();
    const tab = screen.getByRole("tab");
    expect(parseFloat(tab.style.minWidth)).toBe(0);
    const title = within(tab).getByText("a-view-with-a-long-name.ai.yaml");
    expect(title.style.overflow).toBe("hidden");
    expect(title.style.textOverflow).toBe("ellipsis");
    expect(parseFloat(title.style.minWidth)).toBe(0);
  });

  it("shows its Edit toggle as the icon alone when the pane is narrow", () => {
    visit();
    const edit = screen.getByRole("button", { name: "Edit" });
    expect(edit).toHaveTextContent("Edit");
    emit(NARROW_TAB_STRIP - 1);
    expect(screen.getByRole("button", { name: "Edit" })).not.toHaveTextContent("Edit");
    emit(NARROW_TAB_STRIP);
    expect(screen.getByRole("button", { name: "Edit" })).toHaveTextContent("Edit");
  });

  it("leaves splitting to the tab's menu when the pane is narrow", () => {
    visit();
    expect(screen.getByTitle("Split right")).toBeInTheDocument();
    emit(NARROW_TAB_STRIP - 1);
    expect(screen.queryByTitle("Split right")).not.toBeInTheDocument();
  });
});

describe("the view page", () => {
  it("never scrolls sideways: it clips at the window's width", () => {
    visit();
    const page = screen.getByTestId("page-item-view");
    expect(page.style.overflow).toBe("hidden");
    expect(page.style.maxWidth).toBe("100vw");
  });
});
