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

import type { AppItem } from "../api/types";
import { QueryWrap } from "../test/queryWrapper";
import { AgentCopyBar, AppWorkspace } from "./AppWorkspace";

const item = (over: Partial<AppItem>): AppItem => ({
  resource_id: "rca-investigation:c1",
  title: "Sales helper",
  owner: "amy",
  created_time: "",
  created_by: "amy",
  ...over,
});

function renderBar(it: AppItem) {
  render(
    <MemoryRouter>
      <QueryWrap>
        <AgentCopyBar item={it} />
      </QueryWrap>
    </MemoryRouter>,
  );
}

afterEach(() => cleanup());

// plan-wui-publish-and-agent-hub A11: a conversation with an agent shows the
// agent's name and the way back to its page in Agent Hub.
describe("AgentCopyBar", () => {
  it("names the agent and goes back to its page", () => {
    renderBar(item({ published_from: "rca-investigation%3Asrc∕a∕agent.ai.yaml", published_title: "Sales" }));

    expect(screen.getByTestId("agent-copy-bar")).toHaveTextContent("Sales");
    expect(screen.getByRole("link", { name: /回到 Agent Hub/ })).toHaveAttribute(
      "href",
      `/agents/${encodeURIComponent("rca-investigation%3Asrc∕a∕agent.ai.yaml")}`,
    );
  });

  it("goes back to Agent Hub itself when the copy does not say where it came from", () => {
    renderBar(item({ published_title: "" }));

    expect(screen.getByTestId("agent-copy-bar")).toHaveTextContent("Sales helper");
    expect(screen.getByRole("link", { name: /回到 Agent Hub/ })).toHaveAttribute("href", "/agents");
  });
});

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
  it("draws the agent's bar, no list of other chats, and the files tucked away", () => {
    openWorkspace({ published_kind: "agent", published_title: "Sales" });

    expect(screen.getByTestId("agent-copy-bar")).toHaveTextContent("Sales");
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
