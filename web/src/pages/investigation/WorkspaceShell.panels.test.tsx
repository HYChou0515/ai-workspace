// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// Everything live is stubbed to a marker — these tests are about ONE thing:
// which shell panels are showing, and what opens or retracts them.
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: () => <div data-testid="chat" />,
}));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("./TerminalPane", () => ({ TerminalPane: () => null }));
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
  slug: "rca",
  title: "RCA",
  icon: "flame",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: true },
  agent: { picker: [] },
  item: { noun: "Investigation", noun_plural: "Investigations" },
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
  resource_route: "/rca-investigation",
} as unknown as AppManifest;

const item = {
  resource_id: "INC-1",
  title: "Reflow drift",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

function openShell() {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={[]} />
    </MemoryRouter>,
  );
}

/** The gesture a browser actually delivers: click(1) → click(2) → dblclick. */
function doubleClick(el: HTMLElement) {
  fireEvent.click(el, { detail: 1 });
  fireEvent.click(el, { detail: 2 });
  fireEvent.doubleClick(el, { detail: 2 });
}

afterEach(cleanup);

describe("the bottom log strip does not open itself", () => {
  it("opens the item with the log body collapsed, tab row still in reach", async () => {
    openShell();
    expect(await screen.findByRole("button", { name: "Agent log" })).toBeInTheDocument();
    expect(screen.queryByTestId("bottom-body")).not.toBeInTheDocument();
  });
});

describe("clicking a log tab reveals it until you go elsewhere", () => {
  it("peeks the body open on a single click", async () => {
    openShell();
    fireEvent.click(await screen.findByRole("button", { name: "Agent log" }), { detail: 1 });
    expect(await screen.findByTestId("bottom-body")).toBeInTheDocument();
  });

  it("retracts that peek when the next press lands outside the panel", async () => {
    openShell();
    fireEvent.click(await screen.findByRole("button", { name: "Agent log" }), { detail: 1 });
    await screen.findByTestId("bottom-body");
    fireEvent.mouseDown(screen.getByTestId("page-item"));
    await waitFor(() => expect(screen.queryByTestId("bottom-body")).not.toBeInTheDocument());
  });

  it("keeps a peek open while you work INSIDE the panel", async () => {
    openShell();
    fireEvent.click(await screen.findByRole("button", { name: "Agent log" }), { detail: 1 });
    const body = await screen.findByTestId("bottom-body");
    fireEvent.mouseDown(body);
    expect(screen.getByTestId("bottom-body")).toBeInTheDocument();
  });
});

describe("double-clicking a log tab pins it", () => {
  it("survives a press outside once pinned — that is what pinning buys", async () => {
    openShell();
    doubleClick(await screen.findByRole("button", { name: "Agent log" }));
    await screen.findByTestId("bottom-body");
    fireEvent.mouseDown(screen.getByTestId("page-item"));
    expect(screen.getByTestId("bottom-body")).toBeInTheDocument();
  });

  it("collapses when the tab already on show is double-clicked again", async () => {
    openShell();
    const tab = await screen.findByRole("button", { name: "Agent log" });
    doubleClick(tab); // pin
    await screen.findByTestId("bottom-body");
    doubleClick(tab); // same target → collapse
    await waitFor(() => expect(screen.queryByTestId("bottom-body")).not.toBeInTheDocument());
  });

  it("switches instead of collapsing when a DIFFERENT tab is double-clicked", async () => {
    openShell();
    doubleClick(await screen.findByRole("button", { name: "Agent log" })); // pin
    await screen.findByTestId("bottom-body");
    doubleClick(screen.getByRole("button", { name: "Terminal" }));
    // Still open, now showing Terminal.
    expect(screen.getByTestId("bottom-body")).toBeInTheDocument();
  });
});

describe("the chevron stays a one-click toggle", () => {
  it("opens the panel to a state that outlives clicking away", async () => {
    openShell();
    fireEvent.click(await screen.findByRole("button", { name: "toggle bottom panel" }));
    await screen.findByTestId("bottom-body");
    fireEvent.mouseDown(screen.getByTestId("page-item"));
    expect(screen.getByTestId("bottom-body")).toBeInTheDocument();
  });
});
