/**
 * #847/#848 P6 — a shown view file (`*.ai.yaml`) whose view kind offers a
 * `Thumbnail` draws it small in its chat card: lazily, once, and never in the
 * way of the click that opens the live view. A kind with none, or a thumbnail
 * that fails, keeps today's plain file card.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../api/fileService";
import { OpenFileProvider, ViewPageHrefProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import type { ViewThumbnailProps } from "../renderers/entity/viewKindRegistry";
import { registerViewKind, unregisterViewKind } from "../renderers/entity/viewKindRegistry";
import type { ShownFile } from "../renderers/shownFiles";
import { stubIntersectionObserver } from "../test/intersection";
import { makeTestQueryClient, renderWithQuery } from "../test/queryWrapper";
import { ShownFiles } from "./ShownFiles";

const KIND = "thumbtest";
const PLAIN = "plaintest";
const mounts = vi.fn();
const drawn = vi.fn();
let behaviour: "draw" | "fail" | "throw" = "draw";

function TestThumbnail({ spec, path, onFail }: ViewThumbnailProps) {
  useEffect(() => {
    mounts(path);
    if (behaviour === "fail") onFail("the sandbox said no");
  }, [path, onFail]);
  if (behaviour === "throw") throw new Error("broken spec");
  drawn(spec.view, path);
  return <div data-testid="test-thumb">{`small ${spec.view}`}</div>;
}

const files: Record<string, string> = {
  "/views/lots.ai.yaml": `view: ${KIND}\nsource: data/a.csv\n`,
  "/views/table.ai.yaml": `view: ${PLAIN}\n`,
  "/views/broken.ai.yaml": "view: [unclosed\n",
};
const readFile = vi.fn(async (path: string) => {
  const text = files[path];
  if (text === undefined) throw new Error(`no such file ${path}`);
  return { kind: "text" as const, path, size: text.length, text, encoding: "utf-8" as const };
});
const service = { scopeId: "item-1", readFile } as unknown as FileService;

const card = (path: string): ShownFile => ({ path, mime: "text/plain", size: 120 });

let io: ReturnType<typeof stubIntersectionObserver>;
beforeEach(() => {
  io = stubIntersectionObserver();
  behaviour = "draw";
  registerViewKind({ kind: KIND, Component: () => null, Thumbnail: TestThumbnail });
  registerViewKind({ kind: PLAIN, Component: () => null });
});
afterEach(() => {
  cleanup();
  unregisterViewKind(KIND);
  unregisterViewKind(PLAIN);
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function show(list: ShownFile[], openFile = vi.fn()) {
  renderWithQuery(
    <FileServiceProvider value={service}>
      <OpenFileProvider value={openFile}>
        <WorkspaceVisibleProvider value>
          <ShownFiles files={list} fileUrl={(p) => `/api/files${p}`} />
        </WorkspaceVisibleProvider>
      </OpenFileProvider>
    </FileServiceProvider>,
  );
  return openFile;
}

describe("ShownFiles — view thumbnails", () => {
  it("reads nothing and draws nothing before the card scrolls into view", async () => {
    show([card("/views/lots.ai.yaml")]);
    await act(async () => {});
    expect(readFile).not.toHaveBeenCalled();
    expect(mounts).not.toHaveBeenCalled();
    // until then it is today's card: the file icon, the name
    expect(screen.getByText("lots.ai.yaml")).toBeInTheDocument();
  });

  it("an observer's report that the card is still off screen draws nothing", async () => {
    show([card("/views/lots.ai.yaml")]);
    act(() => io.reportOutOfView());
    await act(async () => {});
    expect(readFile).not.toHaveBeenCalled();
    expect(mounts).not.toHaveBeenCalled();
    expect(io.watching()).toBe(1);
  });

  it("drawn, the thumbnail stands in for the file icon, as an image does", async () => {
    show([card("/views/lots.ai.yaml")]);
    expect(document.querySelector('[data-icon="file"]')).not.toBeNull();
    act(() => io.scrollIntoView());
    await screen.findByTestId("test-thumb");
    expect(document.querySelector('[data-icon="file"]')).toBeNull();
    expect(screen.getByText("lots.ai.yaml")).toBeInTheDocument();
  });

  it("with no item workspace to read from (no file service), a view file is today's card", async () => {
    renderWithQuery(<ShownFiles files={[card("/views/lots.ai.yaml")]} />);
    act(() => io.scrollIntoView());
    await act(async () => {});
    expect(readFile).not.toHaveBeenCalled();
    expect(io.watching()).toBe(0);
    expect(screen.getByText("lots.ai.yaml")).toBeInTheDocument();
  });

  it("draws the kind's thumbnail once the card is in view, handed the parsed spec and the path", async () => {
    show([card("/views/lots.ai.yaml")]);
    act(() => io.scrollIntoView());
    expect(await screen.findByTestId("test-thumb")).toHaveTextContent(`small ${KIND}`);
    expect(drawn).toHaveBeenLastCalledWith(KIND, "/views/lots.ai.yaml");
  });

  it("draws it ONCE: scrolling past again neither re-reads nor re-mounts it", async () => {
    show([card("/views/lots.ai.yaml")]);
    act(() => io.scrollIntoView());
    await screen.findByTestId("test-thumb");
    // the observer let go of the card the first time it was seen
    expect(io.watching()).toBe(0);
    act(() => io.scrollIntoView());
    await act(async () => {});
    expect(mounts).toHaveBeenCalledTimes(1);
    expect(readFile).toHaveBeenCalledTimes(1);
  });

  it("still draws after the card changed element: the workspace opened, so the new-tab link became a button", async () => {
    // Found in a real browser: the observer kept watching the link that the
    // button replaced, and the card never drew.
    const ui = (visible: boolean) => (
      <QueryClientProvider client={client}>
        <FileServiceProvider value={service}>
          <ViewPageHrefProvider value={() => "/a/pm/it/view"}>
            <OpenFileProvider value={vi.fn()}>
              <WorkspaceVisibleProvider value={visible}>
                <ShownFiles files={[card("/views/lots.ai.yaml")]} fileUrl={(p) => `/api/files${p}`} />
              </WorkspaceVisibleProvider>
            </OpenFileProvider>
          </ViewPageHrefProvider>
        </FileServiceProvider>
      </QueryClientProvider>
    );
    const client = makeTestQueryClient();
    const { rerender } = render(ui(false));
    expect(screen.getByRole("link", { name: /lots\.ai\.yaml/ })).toBeInTheDocument();
    rerender(ui(true));
    expect(screen.getByRole("button", { name: /lots\.ai\.yaml/ })).toBeInTheDocument();
    act(() => io.scrollIntoView());
    expect(await screen.findByTestId("test-thumb")).toBeInTheDocument();
  });

  it("clicking the card still opens the live view", async () => {
    const openFile = show([card("/views/lots.ai.yaml")]);
    act(() => io.scrollIntoView());
    fireEvent.click(await screen.findByTestId("test-thumb"));
    expect(openFile).toHaveBeenCalledWith("/views/lots.ai.yaml");
  });

  it("the thumbnail takes no pointer events: it is a picture of the view, not the view", async () => {
    show([card("/views/lots.ai.yaml")]);
    act(() => io.scrollIntoView());
    const thumb = await screen.findByTestId("test-thumb");
    expect(thumb.closest("[data-view-thumbnail]")).toHaveStyle({ pointerEvents: "none" });
  });

  it("a thumbnail that reports a failure leaves today's plain card, not a broken box", async () => {
    behaviour = "fail";
    show([card("/views/lots.ai.yaml")]);
    act(() => io.scrollIntoView());
    await waitFor(() => expect(mounts).toHaveBeenCalled());
    await waitFor(() => expect(document.querySelector("[data-view-thumbnail]")).toBeNull());
    expect(screen.getByText("lots.ai.yaml")).toBeInTheDocument();
  });

  it("a thumbnail that throws leaves today's plain card too", async () => {
    behaviour = "throw";
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    show([card("/views/lots.ai.yaml")]);
    act(() => io.scrollIntoView());
    await waitFor(() => expect(readFile).toHaveBeenCalled());
    await act(async () => {});
    expect(document.querySelector("[data-view-thumbnail]")).toBeNull();
    expect(screen.getByRole("button", { name: /lots\.ai\.yaml/ })).toBeInTheDocument();
    quiet.mockRestore();
  });

  it("a view file that does not parse keeps the plain card", async () => {
    show([card("/views/broken.ai.yaml")]);
    act(() => io.scrollIntoView());
    await waitFor(() => expect(readFile).toHaveBeenCalled());
    await act(async () => {});
    expect(document.querySelector("[data-view-thumbnail]")).toBeNull();
    expect(screen.getByText("broken.ai.yaml")).toBeInTheDocument();
  });

  it("a kind that offers no thumbnail keeps today's file card", async () => {
    show([card("/views/table.ai.yaml")]);
    act(() => io.scrollIntoView());
    await waitFor(() => expect(readFile).toHaveBeenCalled());
    await act(async () => {});
    expect(document.querySelector("[data-view-thumbnail]")).toBeNull();
    expect(screen.getByText("table.ai.yaml")).toBeInTheDocument();
  });

  it("a file that is not a view file is never read for a thumbnail", async () => {
    show([card("/out/notes.txt")]);
    act(() => io.scrollIntoView());
    await act(async () => {});
    expect(readFile).not.toHaveBeenCalled();
  });
});
