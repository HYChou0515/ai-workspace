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
const listFiles = vi.fn((..._a: unknown[]) => Promise.resolve([] as unknown[]));
const listDirs = vi.fn((..._a: unknown[]) => Promise.resolve([] as unknown[]));
vi.mock("../api", () => ({
  api: {
    listFiles: (...a: unknown[]) => listFiles(...a),
    listDirs: (...a: unknown[]) => listDirs(...a),
  },
}));

const manifestRef: { current: Record<string, unknown> } = { current: {} };
vi.mock("../hooks/useResources", () => ({
  useAppManifest: () => manifestRef.current,
  useAppItem: () => ({ resource_id: "rca-investigation/1", title: "Oven drift", owner: "u" }),
  useAppItems: () => ({ items: [], isPending: false }),
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
  listFiles.mockClear();
  listDirs.mockClear();
});

describe("AppWorkspace — file loading is decoupled from opening the chat", () => {
  it("opens a chat-first item WITHOUT fetching files (so no sandbox warm on open)", async () => {
    manifestRef.current = manifest("chat");
    renderWorkspace();
    expect(await screen.findByTestId("shell")).toBeInTheDocument();
    expect(listFiles).not.toHaveBeenCalled();
    expect(listDirs).not.toHaveBeenCalled();
  });

  it("still fetches files for an IDE-first item (its editor opens on entry)", async () => {
    manifestRef.current = manifest("ide");
    renderWorkspace();
    await waitFor(() => expect(listFiles).toHaveBeenCalled());
  });
});
