/**
 * #847/#848 P6 — a shown layout's miniature draws each pane's own thumbnail
 * (a view kind that offers one), lazily, once the card is in view; every other
 * pane keeps its filename, and the card still opens the whole arrangement.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../api/fileService";
import { OpenLayoutProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import type { LayoutNode } from "../pages/investigation/paneTree";
import {
  registerViewKind,
  unregisterViewKind,
  type ViewThumbnailProps,
} from "../renderers/entity/viewKindRegistry";
import type { ShownLayout } from "../renderers/shownFiles";
import { stubIntersectionObserver } from "../test/intersection";
import { renderWithQuery } from "../test/queryWrapper";
import { ShownLayoutCard } from "./ShownLayoutCard";

const KIND = "panethumb";
const PLAIN = "paneplain";
const mounts = vi.fn();

function PaneThumbnail({ path, onFail }: ViewThumbnailProps) {
  mounts(path);
  if (path.includes("fails")) queueMicrotask(() => onFail("no"));
  return <div data-testid="pane-thumb">{`thumb of ${path}`}</div>;
}

const files: Record<string, string> = {
  "/v/grid.ai.yaml": `view: ${KIND}\n`,
  "/v/fails.ai.yaml": `view: ${KIND}\n`,
  "/v/table.ai.yaml": `view: ${PLAIN}\n`,
};
const readFile = vi.fn(async (path: string) => {
  const text = files[path];
  if (text === undefined) throw new Error(`no such file ${path}`);
  return { kind: "text" as const, path, size: text.length, text, encoding: "utf-8" as const };
});
const service = { scopeId: "item-1", readFile } as unknown as FileService;

const leaf = (path: string): LayoutNode => ({ type: "leaf", path });
const layout: LayoutNode = {
  type: "split",
  dir: "row",
  ratio: 0.5,
  a: { type: "split", dir: "col", ratio: 0.5, a: leaf("/v/grid.ai.yaml"), b: leaf("/v/fails.ai.yaml") },
  b: { type: "split", dir: "col", ratio: 0.5, a: leaf("/v/table.ai.yaml"), b: leaf("/v/data.csv") },
};
const shown: ShownLayout = {
  layout,
  files: ["/v/grid.ai.yaml", "/v/fails.ai.yaml", "/v/table.ai.yaml", "/v/data.csv"].map((path) => ({
    path,
    mime: "text/plain",
    size: 10,
  })),
};

let io: ReturnType<typeof stubIntersectionObserver>;
beforeEach(() => {
  io = stubIntersectionObserver();
  registerViewKind({ kind: KIND, Component: () => null, Thumbnail: PaneThumbnail });
  registerViewKind({ kind: PLAIN, Component: () => null });
});
afterEach(() => {
  cleanup();
  unregisterViewKind(KIND);
  unregisterViewKind(PLAIN);
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function renderCard(openLayout = vi.fn()) {
  renderWithQuery(
    <FileServiceProvider value={service}>
      <OpenLayoutProvider value={openLayout}>
        <WorkspaceVisibleProvider value>
          <ShownLayoutCard shown={shown} />
        </WorkspaceVisibleProvider>
      </OpenLayoutProvider>
    </FileServiceProvider>,
  );
  return openLayout;
}

const pane = (path: string) => screen.getAllByTestId("shown-layout-pane").find((p) => p.title === path)!;

describe("ShownLayoutCard — pane thumbnails", () => {
  it("reads no pane before the card scrolls into view: every pane is its filename", async () => {
    renderCard();
    await act(async () => {});
    expect(readFile).not.toHaveBeenCalled();
    expect(screen.getAllByTestId("shown-layout-pane").map((p) => p.textContent)).toEqual([
      "grid.ai.yaml",
      "fails.ai.yaml",
      "table.ai.yaml",
      "data.csv",
    ]);
  });

  it("in view, each pane of a kind with a thumbnail draws its own; the rest keep their names", async () => {
    renderCard();
    act(() => io.scrollIntoView());
    expect(await within(pane("/v/grid.ai.yaml")).findByTestId("pane-thumb")).toHaveTextContent(
      "thumb of /v/grid.ai.yaml",
    );
    // a pane whose thumbnail failed, a kind without one, a file that is no view
    await waitFor(() => expect(pane("/v/fails.ai.yaml")).toHaveTextContent("fails.ai.yaml"));
    expect(within(pane("/v/fails.ai.yaml")).queryByTestId("pane-thumb")).toBeNull();
    await waitFor(() => expect(readFile).toHaveBeenCalledWith("/v/table.ai.yaml"));
    expect(pane("/v/table.ai.yaml")).toHaveTextContent("table.ai.yaml");
    expect(pane("/v/data.csv")).toHaveTextContent("data.csv");
    expect(readFile).not.toHaveBeenCalledWith("/v/data.csv");
  });

  it("the card is watched once, not once per pane, and stops being watched when seen", async () => {
    renderCard();
    expect(io.watching()).toBe(1);
    act(() => io.scrollIntoView());
    await within(pane("/v/grid.ai.yaml")).findByTestId("pane-thumb");
    expect(io.watching()).toBe(0);
  });

  it("clicking the card still opens the whole layout", async () => {
    const openLayout = renderCard();
    act(() => io.scrollIntoView());
    fireEvent.click(await screen.findByText("thumb of /v/grid.ai.yaml"));
    expect(openLayout).toHaveBeenCalledWith(layout);
  });
});
