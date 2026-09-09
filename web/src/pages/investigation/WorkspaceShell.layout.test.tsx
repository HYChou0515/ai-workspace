// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import {
  TopBar,
  initialIdeCollapsed,
  initialSidebarState,
  mainSurfaceTabs,
  showAgentPanel,
} from "./WorkspaceShell";

// The TopBar hosts the live PresenceBar (#455), which opens an SSE subscription +
// reads the current user through TanStack Query. These layout tests render TopBar
// bare (no providers) and only exercise the Workspace toggle — stub presence out.
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
// The top bar's access chip reads identity through TanStack Query too; these
// provider-free layout tests only exercise the Workspace toggle, so stub both.
vi.mock("../../hooks/useCurrentUser", () => ({ useCurrentUser: () => "alice" }));
vi.mock("../../hooks/useIsSuperuser", () => ({ useIsSuperuserState: () => ({ isSuperuser: false, groups: [] }) }));

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

describe("initialSidebarState (#785)", () => {
  it("docks the tree for an ide-first App — its files ARE the main stage", () => {
    expect(initialSidebarState("ide", false)).toBe("pinned");
  });

  it("collapses the tree for a views-first App so the chart gets the width", () => {
    expect(initialSidebarState("views", false)).toBe("closed");
  });

  it("collapses on narrow whatever the App is — the tree is a tap-to-open overlay there (#464)", () => {
    // The narrow rule predates this and still wins: four columns do not fit
    // below 768px, so the width question is settled before the App is asked.
    expect(initialSidebarState("ide", true)).toBe("closed");
    expect(initialSidebarState("views", true)).toBe("closed");
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
  isNarrow?: boolean;
  bottomState?: "closed" | "peeked" | "pinned";
  onPanelBottom?: (a: unknown) => void;
  chatCollapsed?: boolean;
  onToggleChat?: () => void;
}) {
  return render(
    <MemoryRouter>
      <TopBar
        item={item}
        manifest={manifest({ workspace: over.workspace })}
        onEditField={vi.fn()}
        isNarrow={over.isNarrow ?? false}
        ideCollapsed={over.ideCollapsed ?? false}
        onToggleIde={over.onToggleIde ?? vi.fn()}
        bottomState={over.bottomState ?? "closed"}
        onPanelBottom={(over.onPanelBottom ?? vi.fn()) as never}
        chatCollapsed={over.chatCollapsed ?? false}
        onToggleChat={over.onToggleChat ?? vi.fn()}
        onCommandPalette={vi.fn()}
        onEdit={vi.fn()}
      />
    </MemoryRouter>,
  );
}

const openView = () => fireEvent.click(screen.getByRole("button", { name: /view/i }));

/**
 * The old control was a lone "Workspace" pill wedged between the breadcrumb
 * chips and the command palette. Three things were wrong with it and users
 * said so: it was shaped and sized exactly like the P1 / triaging / Public
 * STATUS chips beside it so it did not read as a control at all; it wore the
 * accent (a red in this palette, the severity colour) when ON, so "workspace
 * open" looked like a warning; and it duplicated the global nav's own
 * "Workspace" brand link, two of the same word on one screen meaning different
 * things. The log strip and the chat had no equivalent control at all.
 *
 * All three panels now live behind one "View" menu — the place layout settings
 * go, and the place the next one will go too.
 */
describe("TopBar View menu", () => {
  it("offers a View menu when the App has a workspace", () => {
    renderTopBar({ workspace: true });
    expect(screen.getByRole("button", { name: /view/i })).toBeInTheDocument();
  });

  it("no longer parks a bare Workspace pill among the status chips", () => {
    renderTopBar({ workspace: true });
    // Nothing named "workspace" is on the bar until the menu is opened.
    expect(screen.queryByRole("button", { name: /workspace/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /workspace/i })).not.toBeInTheDocument();
  });

  it("holds a toggle per panel once opened", () => {
    renderTopBar({ workspace: true });
    openView();
    expect(screen.getByRole("checkbox", { name: /workspace/i })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /log/i })).toBeInTheDocument();
  });

  it("reports each panel's current state, so the menu can be read at a glance", () => {
    renderTopBar({ workspace: true, ideCollapsed: false, bottomState: "closed" });
    openView();
    expect(screen.getByRole("checkbox", { name: /workspace/i })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /log/i })).not.toBeChecked();
  });

  it("counts a peeked panel as showing", () => {
    renderTopBar({ workspace: true, bottomState: "peeked" });
    openView();
    expect(screen.getByRole("checkbox", { name: /log/i })).toBeChecked();
  });

  it("toggles the workspace from the menu", () => {
    const onToggleIde = vi.fn();
    renderTopBar({ workspace: true, ideCollapsed: true, onToggleIde });
    openView();
    fireEvent.click(screen.getByRole("checkbox", { name: /workspace/i }));
    expect(onToggleIde).toHaveBeenCalledTimes(1);
  });

  it("toggles the log strip from the menu", () => {
    const onPanelBottom = vi.fn();
    renderTopBar({ workspace: true, onPanelBottom });
    openView();
    fireEvent.click(screen.getByRole("checkbox", { name: /log/i }));
    expect(onPanelBottom).toHaveBeenCalledWith({ type: "toggle" });
  });

  // Seen in a real browser: an unstyled checkbox renders in the platform blue,
  // which is nowhere in this warm paper palette and reads as a foreign control
  // dropped into the menu. Same treatment as the font-size slider.
  it("draws its checkboxes in the app accent, not the browser default blue", () => {
    renderTopBar({ workspace: true });
    openView();
    expect(screen.getByRole("checkbox", { name: /workspace/i }).style.accentColor).toBe(
      "var(--accent)",
    );
  });

  // The item-details button next to it is already a gear. Two gears side by
  // side, one for the item and one for the layout, is a coin toss.
  it("does not wear the same gear glyph as the item-details button beside it", () => {
    renderTopBar({ workspace: true });
    const view = screen.getByRole("button", { name: /view/i });
    const edit = screen.getByRole("button", { name: /edit item details/i });
    const glyph = (b: HTMLElement) => b.querySelector("svg")?.getAttribute("data-icon");
    expect(glyph(view)).toBeTruthy();
    expect(glyph(view)).not.toBe(glyph(edit));
  });

  /**
   * Measured in a real browser at 700px: the Chat row was inert AND wrong. On a
   * narrow shell #464 already makes the workspace and the chat mutually
   * exclusive — whichever the Workspace toggle is not showing — so a separate
   * chat toggle governs nothing. Ticking it changed no pixels, and the row
   * reported "Chat ✓" while no chat was on screen.
   *
   * One control per decision: on narrow the Workspace row IS the switch, and
   * its tooltip already says the chat expands to fill.
   */
  it("drops the Chat row on a narrow shell, where the workspace row is the switch", () => {
    renderTopBar({ workspace: true, isNarrow: true });
    openView();
    expect(screen.queryByRole("checkbox", { name: /chat/i })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /workspace/i })).toBeInTheDocument();
  });

  it("keeps the Chat row on a wide shell, where the two sit side by side", () => {
    renderTopBar({ workspace: true, isNarrow: false });
    openView();
    expect(screen.getByRole("checkbox", { name: /chat/i })).toBeInTheDocument();
  });

  it("hides the menu for a chat-only App — there are no panels to arrange", () => {
    renderTopBar({ workspace: false });
    expect(screen.queryByRole("button", { name: /view/i })).not.toBeInTheDocument();
  });

  it("hides the IDE-only command palette while the workspace is collapsed", () => {
    renderTopBar({ workspace: true, ideCollapsed: true });
    expect(screen.queryByRole("button", { name: /go to file/i })).not.toBeInTheDocument();
  });

  it("shows the command palette when the workspace is open", () => {
    renderTopBar({ workspace: true, ideCollapsed: false });
    expect(screen.getByRole("button", { name: /go to file/i })).toBeInTheDocument();
  });

  // Replaces the old "accent fill = pressed" assertion: a checkbox says on/off
  // in its own right, so the state no longer has to be inferred from a colour —
  // which is what let the ON state wear the severity red in the first place.
  it("says what each toggle does to the chat, so it is not a mystery control", () => {
    renderTopBar({ workspace: true, ideCollapsed: false });
    openView();
    const ws = screen.getByRole("checkbox", { name: /workspace/i });
    expect(ws.closest("label")?.getAttribute("title") ?? "").toMatch(/chat/i);
  });
});

describe("TopBar item title stays on one line (#fe-responsive)", () => {
  // Measured in a real browser at 1024x768 with a long title: the title wrapped
  // to three lines inside a bar whose height is pinned to 52px on wide
  // viewports, and `page-item` (overflow: hidden) sliced the extra lines off —
  // the first line vanished above the bar and the last below it, leaving the
  // middle fragment overlapping the row's controls. A fixed-height bar has to
  // ellipsize, not wrap.
  it("ellipsizes on one line instead of wrapping out of the fixed-height bar", () => {
    renderTopBar({ workspace: true });
    const title = screen.getByTestId("topbar-title") as HTMLElement;
    expect(title.style.whiteSpace).toBe("nowrap");
    expect(title.style.overflow).toBe("hidden");
    expect(title.style.textOverflow).toBe("ellipsis");
    expect(title.style.minWidth).toBe("0");
  });

  it("keeps the full title reachable as a tooltip once it ellipsizes", () => {
    renderTopBar({ workspace: true });
    expect(screen.getByTestId("topbar-title")).toHaveAttribute("title", "Oven drift");
  });
});

describe("TopBar takes its layout mode from the shell, not the viewport (#fe-responsive)", () => {
  // The bar used to call `useIsNarrow()` itself, i.e. ask the WINDOW. But the
  // bar lives inside the shell, and the shell does not own the window: a
  // chat-first App puts a 240px rail beside it. Measured in a real browser at
  // 768px, the shell had 528px and the bar still laid its controls out in one
  // nowrap row — which `page-item` then clipped. The shell measures itself and
  // tells the bar; the bar must honour what it is told.
  it("wraps its controls when the SHELL says narrow, regardless of the viewport", () => {
    renderTopBar({ workspace: true, isNarrow: true });
    const row = screen.getByTestId("topbar");
    expect(row.style.flexWrap).toBe("wrap");
    expect(row.style.height).toBe("auto");
  });

  it("keeps one nowrap row when the shell says wide", () => {
    renderTopBar({ workspace: true, isNarrow: false });
    const row = screen.getByTestId("topbar");
    expect(row.style.flexWrap).toBe("nowrap");
    expect(row.style.height).toBe("52px");
  });
});

describe("TopBar command palette yields width before the title does (#fe-responsive)", () => {
  // Once the title ellipsizes instead of wrapping (see above), the question
  // becomes what it ellipsizes DOWN TO. Measured at 1024x768 it got 17px — a
  // bare "…" — because the palette button was `flex: 0 0 auto` at a hard 320px
  // and simply refused to give anything back, so every pixel of shrink landed
  // on the one element that could take it. The palette is a convenience with a
  // keyboard equivalent; the item's own name is the page's subject. The
  // palette shrinks first.
  it("makes the palette shrinkable on a wide row instead of pinning it at 320px", () => {
    renderTopBar({ workspace: true, ideCollapsed: false, isNarrow: false });
    const palette = screen.getByRole("button", { name: /go to file/i });
    expect(palette.style.flexShrink).toBe("1");
    expect(palette.style.flexBasis).toBe("320px");
    expect(palette.style.minWidth).toBe("140px");
  });

  it("still gives the palette its own full-width row on narrow", () => {
    renderTopBar({ workspace: true, ideCollapsed: false, isNarrow: true });
    const palette = screen.getByRole("button", { name: /go to file/i });
    expect(palette.style.flexBasis).toBe("100%");
  });
});
