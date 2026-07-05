// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { TopBar, initialIdeCollapsed, mainSurfaceTabs, showAgentPanel } from "./WorkspaceShell";

afterEach(cleanup);

function manifest(over: {
  workspace?: boolean;
  primary_surface?: "chat" | "ide" | "views";
  views?: string[];
  default_tabs?: string[];
}): AppManifest {
  return {
    slug: "x",
    title: "X",
    icon: "",
    color: "",
    function: { workspace: over.workspace ?? true, sandbox: true, terminal: false },
    agent: { picker: [] },
    item: { noun: "Item", noun_plural: "Items" },
    layout: {
      breadcrumb: [],
      statusbar: [],
      list: [],
      default_tabs: over.default_tabs ?? [],
      views: over.views,
      primary_surface: over.primary_surface ?? "chat",
    },
    labels: {},
    fields: [],
    field_styles: {},
  } as unknown as AppManifest;
}

describe("initialIdeCollapsed (#159)", () => {
  it("collapses the IDE for a chat-first App so chat is the main stage", () => {
    expect(initialIdeCollapsed(manifest({ primary_surface: "chat" }))).toBe(true);
  });

  it("opens the IDE for an ide-first App (RCA's evidence/brief flow)", () => {
    expect(initialIdeCollapsed(manifest({ primary_surface: "ide" }))).toBe(false);
  });

  it("treats a no-workspace App as collapsed — there is no IDE, chat fills the row", () => {
    expect(
      initialIdeCollapsed(manifest({ workspace: false, primary_surface: "ide" })),
    ).toBe(true);
  });

  it("opens the workspace up front for a views-first App (#419 §B5)", () => {
    expect(initialIdeCollapsed(manifest({ primary_surface: "views", views: ["/views/board.ai.yaml"] }))).toBe(false);
  });
});

describe("showAgentPanel (#464)", () => {
  it("always shows the agent beside the IDE on a wide viewport", () => {
    expect(showAgentPanel(false, false)).toBe(true); // IDE open, wide
    expect(showAgentPanel(false, true)).toBe(true); // chat filling, wide
  });

  it("hides the agent on a narrow viewport while the IDE is up (mutual exclusion)", () => {
    // Narrow + IDE showing (chat not filling) → the fixed-width agent would force
    // horizontal overflow, so it's hidden until the IDE is collapsed.
    expect(showAgentPanel(true, false)).toBe(false);
  });

  it("shows the agent full-width on narrow once the IDE is collapsed", () => {
    expect(showAgentPanel(true, true)).toBe(true);
  });
});

describe("mainSurfaceTabs (#419 §B5)", () => {
  it("opens layout.views for a views-first App instead of default_tabs", () => {
    const m = manifest({
      primary_surface: "views",
      views: ["/views/board.ai.yaml", "/views/gantt.ai.yaml"],
      default_tabs: ["/README.md"],
    });
    expect(mainSurfaceTabs(m)).toEqual(["/views/board.ai.yaml", "/views/gantt.ai.yaml"]);
  });

  it("falls back to default_tabs for a non-views App (or empty views)", () => {
    expect(mainSurfaceTabs(manifest({ primary_surface: "ide", default_tabs: ["/SOP.md"] }))).toEqual([
      "/SOP.md",
    ]);
    expect(mainSurfaceTabs(manifest({ primary_surface: "views", views: [], default_tabs: ["/x.md"] }))).toEqual([
      "/x.md",
    ]);
  });
});

const item = {
  resource_id: "rca-investigation/1",
  title: "Oven drift",
  owner: "u1",
} as unknown as AppItem;

function renderTopBar(over: {
  workspace?: boolean;
  ideCollapsed?: boolean;
  onToggleIde?: () => void;
}) {
  return render(
    <MemoryRouter>
      <TopBar
        item={item}
        manifest={manifest({ workspace: over.workspace })}
        onEditField={vi.fn()}
        ideCollapsed={over.ideCollapsed ?? false}
        onToggleIde={over.onToggleIde ?? vi.fn()}
        onCommandPalette={vi.fn()}
        onEdit={vi.fn()}
      />
    </MemoryRouter>,
  );
}

describe("TopBar Workspace toggle (#159)", () => {
  it("offers a discoverable 'Workspace' toggle when the App has a file IDE", () => {
    renderTopBar({ workspace: true });
    expect(screen.getByRole("button", { name: /workspace/i })).toBeInTheDocument();
  });

  it("toggles the IDE when the Workspace button is clicked", () => {
    const onToggleIde = vi.fn();
    renderTopBar({ workspace: true, ideCollapsed: true, onToggleIde });
    fireEvent.click(screen.getByRole("button", { name: /workspace/i }));
    expect(onToggleIde).toHaveBeenCalledTimes(1);
  });

  it("hides the Workspace toggle for a chat-only App (no IDE to toggle)", () => {
    renderTopBar({ workspace: false });
    expect(screen.queryByRole("button", { name: /workspace/i })).not.toBeInTheDocument();
  });

  it("hides the IDE-only command palette while the workspace is collapsed", () => {
    renderTopBar({ workspace: true, ideCollapsed: true });
    expect(screen.queryByRole("button", { name: /go to file/i })).not.toBeInTheDocument();
  });

  it("shows the command palette when the workspace is open", () => {
    renderTopBar({ workspace: true, ideCollapsed: false });
    expect(screen.getByRole("button", { name: /go to file/i })).toBeInTheDocument();
  });

  it("names the chat effect in the tooltip so the toggle isn't a mystery control", () => {
    // #: the control reads as "Workspace", but its visible effect is on the chat
    // (it fills the row when the IDE folds away). Spell that out so it's obvious.
    renderTopBar({ workspace: true, ideCollapsed: false });
    expect(screen.getByRole("button", { name: /workspace/i }).getAttribute("title")).toMatch(
      /chat/i,
    );
  });
});
