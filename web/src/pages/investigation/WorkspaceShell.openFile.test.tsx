// @vitest-environment happy-dom
/**
 * Folding the workspace is the user's choice, and opening a file does not
 * overrule it.
 *
 * A chat-first App opens with the IDE folded (`layout.primary_surface: "chat"`),
 * and folding UNMOUNTS it — so a tab opened while folded is off screen. The
 * answer is NOT to unfold on the user's behalf: callers that can be reached while
 * folded ask `useWorkspaceVisible()` first and offer the file another way (the
 * chat's shown-file card becomes a new-tab link). This pins the half that lives
 * in the shell: the fold survives an `openFile`.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { useOpenFile, useWorkspaceVisible } from "../../hooks/openFile";
import { WorkspaceShell } from "./WorkspaceShell";

// The chat is where the affordance lives, so stand in for it with a button that
// calls the published opener — the same seam `ShownFiles` uses.
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: () => {
    const openFile = useOpenFile();
    const visible = useWorkspaceVisible();
    return (
      <>
        <button
          type="button"
          data-testid="open-from-chat"
          onClick={() => openFile?.("/out/sine.png")}
        >
          show file
        </button>
        <span data-testid="ws-visible">{visible ? "yes" : "no"}</span>
      </>
    );
  },
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
    // Chat-first ⇒ the IDE starts folded, which is the whole point here.
    primary_surface: "chat",
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
  title: "Sine wave demo",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

function open() {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={[{ path: "/out/sine.png", size: 9 }]} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  localStorage.clear();
  cleanup();
});

describe("WorkspaceShell — opening a file from the chat", () => {
  it("leaves the workspace folded rather than unfolding it for the user", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("open-from-chat")).toBeInTheDocument());
    expect(screen.queryByTitle("Search files")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("open-from-chat"));

    await waitFor(() => expect(screen.getByTestId("open-from-chat")).toBeInTheDocument());
    expect(screen.queryByTitle("Search files")).not.toBeInTheDocument();
  });

  it("tells the chat whether the workspace is on screen", async () => {
    // What the card branches on. Folded ⇒ no pane to open into.
    open();
    await waitFor(() => expect(screen.getByTestId("ws-visible")).toBeInTheDocument());
    expect(screen.getByTestId("ws-visible")).toHaveTextContent("no");
  });
});
