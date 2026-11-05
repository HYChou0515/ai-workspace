// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const live: { item: Record<string, unknown> } = { item: {} };
vi.mock("../hooks/useResources", async (orig) => ({
  ...(await orig<typeof import("../hooks/useResources")>()),
  useAppManifest: () => ({
    slug: "rca",
    resource_route: "/rca-investigation",
    function: { workspace: true },
    layout: { primary_surface: "chat" },
  }),
  useAppItem: () => live.item,
}));
vi.mock("../hooks/useInvestigation", () => ({
  useFiles: () => ({ kind: "ready", items: [], dirs: [], unwalked: [], truncated: false, refresh: () => {} }),
}));
vi.mock("../components/ChatListRail", () => ({ ChatListRail: () => <div data-testid="chat-rail" /> }));
vi.mock("./investigation/WorkspaceShell", () => ({
  initialIdeCollapsed: () => false,
  WorkspaceShell: ({ ideCollapsed }: { ideCollapsed: boolean }) => (
    <div data-testid="shell" data-collapsed={String(ideCollapsed)} />
  ),
}));

import { QueryWrap } from "../test/queryWrapper";
import { AppWorkspace } from "./AppWorkspace";

afterEach(() => cleanup());

function openWorkspace(it: Record<string, unknown>) {
  live.item = { resource_id: "rca-investigation:c1", title: "T", owner: "amy", ...it };
  render(
    <MemoryRouter initialEntries={["/a/rca/rca-investigation%3Ac1"]}>
      <QueryWrap>
        <Routes>
          <Route path="/a/:slug/:itemId" element={<AppWorkspace />} />
        </Routes>
      </QueryWrap>
    </MemoryRouter>,
  );
}

describe("a conversation with an agent opens conversation-first (A11)", () => {
  it("is the shell alone — no second header, no list of other chats, the files tucked away", () => {
    openWorkspace({ published_kind: "agent", published_title: "Sales" });

    // Its controls are in the shell's own top bar (WorkspaceShell.agentCopy).
    expect(screen.queryByTestId("agent-copy-bar")).toBeNull();
    expect(screen.queryByTestId("chat-rail")).toBeNull();
    expect(screen.getByTestId("shell")).toHaveAttribute("data-collapsed", "true");
  });

  it("an ordinary item of the same App keeps its own framing", () => {
    openWorkspace({});

    expect(screen.queryByTestId("agent-copy-bar")).toBeNull();
    expect(screen.getByTestId("chat-rail")).toBeInTheDocument();
    expect(screen.getByTestId("shell")).toHaveAttribute("data-collapsed", "false");
  });

  it("a WUI's copy is an ordinary item", () => {
    openWorkspace({ published_kind: "wui", published_title: "Board" });

    expect(screen.queryByTestId("agent-copy-bar")).toBeNull();
  });
});
