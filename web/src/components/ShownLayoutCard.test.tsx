/**
 * `show_file(layout=…)` in the chat (#847 PR 3 P4): one card for the whole
 * arrangement, drawn as a miniature of its panes, that opens it in one click.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OpenLayoutProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import type { LayoutNode } from "../pages/investigation/paneTree";
import type { ShownLayout } from "../renderers/shownFiles";
import { ShownLayoutCard } from "./ShownLayoutCard";

const leaf = (path: string): LayoutNode => ({ type: "leaf", path });
const shown: ShownLayout = {
  layout: {
    type: "split",
    dir: "row",
    ratio: 0.6,
    a: { type: "split", dir: "col", ratio: 0.5, a: leaf("/v/grid.ai.yaml"), b: leaf("/v/scatter.ai.yaml") },
    b: leaf("/v/table.csv"),
  },
  files: [
    { path: "/v/grid.ai.yaml", mime: "text/plain", size: 100 },
    { path: "/v/scatter.ai.yaml", mime: "text/plain", size: 100 },
    { path: "/v/table.csv", mime: "text/csv", size: 100 },
  ],
  caption: "fail rate, linked",
};

afterEach(cleanup);

function renderCard(opts: { openLayout?: (l: LayoutNode) => void; visible?: boolean; href?: string } = {}) {
  const ui = <ShownLayoutCard shown={shown} href={opts.href} />;
  if (!opts.openLayout) return render(ui);
  return render(
    <OpenLayoutProvider value={opts.openLayout}>
      <WorkspaceVisibleProvider value={opts.visible ?? true}>{ui}</WorkspaceVisibleProvider>
    </OpenLayoutProvider>,
  );
}

describe("ShownLayoutCard", () => {
  it("is ONE card with a pane per file, in the layout's own arrangement", () => {
    renderCard();
    expect(screen.getAllByTestId("shown-layout")).toHaveLength(1);
    const panes = screen.getAllByTestId("shown-layout-pane");
    expect(panes.map((p) => p.textContent)).toEqual([
      "grid.ai.yaml",
      "scatter.ai.yaml",
      "table.csv",
    ]);
    expect(screen.getByText("fail rate, linked")).toBeInTheDocument();
  });

  it("draws each split at its ratio, along its direction", () => {
    renderCard();
    const splits = screen.getAllByTestId("shown-layout-split");
    // Root: a row, the left side taking 60%.
    expect(splits[0]).toHaveStyle({ flexDirection: "row" });
    expect(splits[0]!.children[0]).toHaveStyle({ flexGrow: "0.6" });
    expect(splits[1]).toHaveStyle({ flexDirection: "column" });
  });

  it("opens the layout in the workspace when the workspace is on screen", () => {
    const openLayout = vi.fn();
    renderCard({ openLayout });
    fireEvent.click(screen.getByRole("button"));
    expect(openLayout).toHaveBeenCalledWith(shown.layout);
  });

  it("with the workspace folded away, follows the link instead of opening off screen", () => {
    const openLayout = vi.fn();
    renderCard({ openLayout, visible: false, href: "/a/x/items/1/view?layout=abc" });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/a/x/items/1/view?layout=abc");
  });

  it("offers no control at all when there is nowhere to open it", () => {
    renderCard();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
