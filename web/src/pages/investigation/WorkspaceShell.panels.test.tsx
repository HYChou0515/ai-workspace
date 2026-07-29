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

function openShell(files: { path: string; size: number }[] = []) {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={files as never} />
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

/**
 * The file sidebar gets the same three states as the log strip, driven from the
 * activity rail. The rail is 50px of always-there icons, so a collapsed sidebar
 * is never a dead end — and unlike the bottom strip the icons do not move when
 * the sidebar opens beside them, so the double click is safe by construction.
 */
describe("the file sidebar answers to the activity rail", () => {
  const rail = (title: string) => screen.getByTitle(title);
  const pane = () => screen.queryByTestId("sidebar-pane");

  it("opens docked for an ide-first App", async () => {
    openShell();
    await screen.findByTitle("Files");
    expect(pane()).toBeInTheDocument();
  });

  it("collapses when the icon already on show is double-clicked, rail still there", async () => {
    openShell();
    doubleClick(await screen.findByTitle("Files"));
    await waitFor(() => expect(pane()).not.toBeInTheDocument());
    expect(rail("Files")).toBeVisible();
    expect(rail("Search files")).toBeVisible();
  });

  it("re-opens temporarily on a single click, and retracts on a press outside", async () => {
    openShell();
    doubleClick(await screen.findByTitle("Files")); // collapse
    await waitFor(() => expect(pane()).not.toBeInTheDocument());

    fireEvent.click(rail("Search files"), { detail: 1 });
    await waitFor(() => expect(pane()).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByTestId("page-item"));
    await waitFor(() => expect(pane()).not.toBeInTheDocument());
  });

  it("stays put once pinned by a double click", async () => {
    openShell();
    doubleClick(await screen.findByTitle("Files")); // collapse
    await waitFor(() => expect(pane()).not.toBeInTheDocument());

    doubleClick(rail("Search files")); // pin
    await waitFor(() => expect(pane()).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByTestId("page-item"));
    expect(pane()).toBeInTheDocument();
  });

  it("switches content without closing when another icon is clicked while pinned", async () => {
    openShell();
    await screen.findByTitle("Files");
    fireEvent.click(rail("Search files"), { detail: 1 });
    expect(pane()).toBeInTheDocument();
    fireEvent.click(rail("Files"), { detail: 1 });
    expect(pane()).toBeInTheDocument();
  });

  // Same gesture-ordering trap as the log strip's tabs: the clicks inside the
  // double click have already moved `mode` to the new pane, so judging
  // "same target?" against the live value turns every double-click switch
  // into a collapse.
  it("switches rather than collapsing when a DIFFERENT icon is double-clicked while pinned", async () => {
    openShell();
    await screen.findByTitle("Files"); // pinned, showing Files
    doubleClick(rail("Search files"));
    expect(pane()).toBeInTheDocument();
  });

  // The point of glancing at the tree is to open something. Once the file is
  // on screen the tree has served its purpose and gets out of the way — the
  // "go and do something else" rule, where the something else started inside
  // the panel. A PINNED tree of course stays.
  it("retracts a peeked tree once a file is opened from it", async () => {
    openShell([{ path: "brief.md", size: 10 }]);
    doubleClick(await screen.findByTitle("Files")); // collapse
    await waitFor(() => expect(pane()).not.toBeInTheDocument());
    fireEvent.click(rail("Files"), { detail: 1 }); // peek
    await waitFor(() => expect(pane()).toBeInTheDocument());

    fireEvent.click(await screen.findByText("brief.md"));
    await waitFor(() => expect(pane()).not.toBeInTheDocument());
  });

  it("keeps a pinned tree open when a file is opened from it", async () => {
    openShell([{ path: "brief.md", size: 10 }]);
    await screen.findByTitle("Files"); // starts pinned
    fireEvent.click(await screen.findByText("brief.md"));
    expect(pane()).toBeInTheDocument();
  });

  // Same rule as the log strip: a temporary glance floats over the editor
  // rather than reflowing it; only a pin takes its own column.
  it("floats a peeked sidebar and docks a pinned one", async () => {
    openShell();
    await screen.findByTitle("Files");
    expect((pane() as HTMLElement).style.position).toBe(""); // docked by default

    doubleClick(rail("Files")); // collapse
    await waitFor(() => expect(pane()).not.toBeInTheDocument());
    fireEvent.click(rail("Files"), { detail: 1 }); // peek
    await waitFor(() => expect(pane()).toBeInTheDocument());
    expect((pane() as HTMLElement).style.position).toBe("absolute");
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
