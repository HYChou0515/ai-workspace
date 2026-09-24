// @vitest-environment happy-dom
/**
 * `show_file(layout=…)`'s card opens its arrangement in the workspace's own
 * split panes (#847 Q17). The placement is `paneTree.placeLayout`, unit-tested
 * shape by shape; this pins the half that lives in the shell — that it
 * PUBLISHES the opener the chat's card calls, and that calling it splits.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { useOpenLayout, useViewPageHref } from "../../hooks/openFile";
import { viewPageHref } from "../../lib/viewPage";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// Stand in for the chat with a button that calls the published opener — the
// same seam `ShownLayoutCard` uses.
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: () => {
    const openLayout = useOpenLayout();
    const viewHref = useViewPageHref();
    return (
      <>
      <span data-testid="view-href">{viewHref?.({ path: "/v/a.md" }) ?? "none"}</span>
      <button
        type="button"
        data-testid="open-layout-from-chat"
        disabled={!openLayout}
        onClick={() =>
          openLayout?.({
            type: "split",
            dir: "row",
            ratio: 0.5,
            a: { type: "leaf", path: "/v/a.md" },
            b: { type: "leaf", path: "/v/b.md" },
          })
        }
      >
        open layout
      </button>
      </>
    );
  },
}));
vi.mock("../../renderers/FileView", () => ({
  FileView: ({ path }: { path: string }) => <div data-testid="file-view">{path}</div>,
}));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));
vi.mock("../../hooks/useIsSuperuser", () => ({
  useIsSuperuser: () => true,
  useIsSuperuserState: () => ({ isSuperuser: true, ready: true }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "root",
  useCurrentUserState: () => ({ id: "root", ready: true }),
}));

const manifest = {
  slug: "playground",
  title: "Playground",
  icon: "sparkle",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: false },
  agent: { picker: [] },
  item: { noun: "Scratch", noun_plural: "Scratches" },
  layout: {
    breadcrumb: [],
    statusbar: [],
    list: [],
    default_tabs: [],
    primary_surface: "ide",
    chat_switcher: false,
  },
  labels: {},
  fields: [],
  field_styles: {},
  profiles: [],
  default_profile: "default",
  resource_route: "/playground-item",
} as unknown as AppManifest;

const item = {
  resource_id: "PG-1",
  title: "Layout demo",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

afterEach(() => {
  localStorage.clear();
  cleanup();
});

describe("WorkspaceShell — opening a layout from the chat", () => {
  it("publishes the item's editor-area page for the chat's cards (#847 Q5.3)", async () => {
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={manifest} item={item} files={[]} />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("view-href")).toHaveTextContent(
      viewPageHref("playground", "PG-1", { path: "/v/a.md" }),
    );
  });

  it("splits the editor area into the card's panes", async () => {
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell
          manifest={manifest}
          item={item}
          files={[
            { path: "/v/a.md", size: 1 },
            { path: "/v/b.md", size: 1 },
          ]}
        />
      </MemoryRouter>,
    );
    const button = await screen.findByTestId("open-layout-from-chat");
    expect(button).toBeEnabled();
    expect(screen.getAllByTestId("editor-group")).toHaveLength(1);

    fireEvent.click(button);

    await waitFor(() => expect(screen.getAllByTestId("editor-group")).toHaveLength(2));
    expect(screen.getAllByTestId("file-view").map((v) => v.textContent)).toEqual([
      "/v/a.md",
      "/v/b.md",
    ]);
  });
});
