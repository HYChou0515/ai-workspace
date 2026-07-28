// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../test/queryWrapper";
import { AppWorkspace } from "./AppWorkspace";

// Real `useFiles` runs here (NOT mocked) so we can observe whether opening an
// item actually hits the file endpoints — the coupling we're removing. The
// file-tree fetch is what warms the sandbox on the backend, so a chat-first
// item must open without touching it.
// One endpoint now: files and folders come from a single workspace traversal,
// so "did opening the item touch the file endpoints" is one spy, not two.
const getTree = vi.fn((..._a: unknown[]) =>
  Promise.resolve({ files: [] as unknown[], dirs: [] as unknown[] }),
);
vi.mock("../api", () => ({
  api: { getTree: (...a: unknown[]) => getTree(...a) },
}));

const manifestRef: { current: Record<string, unknown> } = { current: {} };
vi.mock("../hooks/useResources", () => ({
  useAppManifest: () => manifestRef.current,
  useAppItem: () => ({ resource_id: "rca-investigation/1", title: "Oven drift", owner: "u" }),
  useAppItems: () => ({ items: [], isPending: false }),
  useApps: () => [],
}));
vi.mock("./investigation/WorkspaceShell", () => ({
  WorkspaceShell: () => <div data-testid="shell">shell</div>,
  initialIdeCollapsed: (m: { function: { workspace: boolean }; layout: { primary_surface: string } }) =>
    !m.function.workspace || m.layout.primary_surface === "chat",
}));

function manifest(primarySurface: "chat" | "ide"): Record<string, unknown> {
  return {
    slug: "rca",
    title: "RCA",
    resource_route: "/rca-investigation",
    function: { workspace: true },
    layout: { primary_surface: primarySurface, default_tabs: [] },
  };
}

function renderWorkspace() {
  return renderWithQuery(
    <MemoryRouter initialEntries={["/a/rca/rca-investigation%2F1"]}>
      <Routes>
        <Route path="/a/:slug/:itemId" element={<AppWorkspace />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  getTree.mockClear();
});

describe("AppWorkspace — file loading is decoupled from opening the chat", () => {
  it("opens a chat-first item WITHOUT fetching files (so no sandbox warm on open)", async () => {
    manifestRef.current = manifest("chat");
    renderWorkspace();
    expect(await screen.findByTestId("shell")).toBeInTheDocument();
    expect(getTree).not.toHaveBeenCalled();
  });

  it("still fetches files for an IDE-first item (its editor opens on entry)", async () => {
    manifestRef.current = manifest("ide");
    renderWorkspace();
    await waitFor(() => expect(getTree).toHaveBeenCalled());
  });

  it("ignores a persisted expanded-IDE value — a new tab opens with the workspace tucked", async () => {
    manifestRef.current = manifest("chat");
    localStorage.setItem("layout:ide-collapsed:rca", "false"); // a prior session left it expanded
    renderWorkspace();
    expect(await screen.findByTestId("shell")).toBeInTheDocument();
    expect(getTree).not.toHaveBeenCalled(); // still collapsed → no file fetch, no warm
  });
});
