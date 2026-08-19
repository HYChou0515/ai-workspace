// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BREAKPOINTS } from "../lib/breakpoints";
import { QueryWrap } from "../test/queryWrapper";
import { ChatListRail } from "./ChatListRail";

const items = [
  { resource_id: "rca-investigation/1", title: "Oven drift", owner: "me" },
  { resource_id: "rca-investigation/2", title: "Sensor noise", owner: "me" },
  { resource_id: "rca-investigation/9", title: "From a teammate", owner: "someone-else" },
];
const manifest = {
  item: { noun: "Project", noun_plural: "Projects", create_label: "Start a Project" },
};
vi.mock("../hooks/useResources", () => ({
  useAppItems: () => ({ items, isPending: false }),
  useAppManifest: () => manifest,
  useApps: () => [
    { slug: "rca", title: "RCA" },
    { slug: "pm", title: "Product" },
  ],
}));
const newChat = vi.fn();
vi.mock("../hooks/useCreateChat", () => ({ useCreateChat: () => ({ mutate: newChat, isPending: false }) }));
const chatActions = { rename: vi.fn(), remove: vi.fn(), busy: false };
vi.mock("../hooks/useChatActions", () => ({ useChatActions: () => chatActions }));
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: () => "me" }));
const directory = [
  { id: "me", name: "Me", section: "", email: "", photo_url: null },
  { id: "someone-else", name: "Sam Teammate", section: "", email: "", photo_url: null },
];
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => directory,
  useUser: (id: string) =>
    directory.find((u) => u.id === id) ?? { id, name: id, section: "", email: "", photo_url: null },
}));
vi.mock("./ShareChatDialog", () => ({ ShareChatDialog: () => <div data-testid="share-dialog" /> }));

afterEach(cleanup);

// The rail tucks itself when the viewport can't hold it AND the workspace shell
// beside it (#fe-responsive). happy-dom's matchMedia does not read
// `window.innerWidth`, so stand in for it — the same stub shape AppDashboard's
// responsive tests use, extended to answer a `(min-width: Npx)` query.
const realMatchMedia = window.matchMedia;
function stubViewport(width: number) {
  window.matchMedia = ((q: string) => {
    const min = /\(min-width:\s*(\d+)px\)/.exec(q);
    const max = /\(max-width:\s*(\d+)px\)/.exec(q);
    const matches = min ? width >= Number(min[1]) : max ? width <= Number(max[1]) : false;
    return {
      matches,
      media: q,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => true,
    };
  }) as unknown as typeof window.matchMedia;
}

// Default to a desktop width so the base tests exercise the OPEN rail; the
// responsive describe below sets its own widths.
beforeEach(() => stubViewport(BREAKPOINTS.shell + BREAKPOINTS.chatRail + 200));
afterEach(() => {
  window.matchMedia = realMatchMedia;
});

function renderRail(currentId = "rca-investigation/1") {
  return render(
    <QueryWrap>
      <MemoryRouter>
        <ChatListRail slug="rca" resourceRoute="/rca-investigation" currentId={currentId} />
      </MemoryRouter>
    </QueryWrap>,
  );
}

describe("ChatListRail", () => {
  it("offers the same platform destinations the global switcher does", () => {
    // The rail kept its own hardcoded list and had silently fallen behind: no
    // My resources at all, and no way to express the conditional entries. Both
    // menus now render one shared, viewer-aware list, so a destination cannot
    // exist in one and not the other.
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /platform menu/i }));

    // Assert the DESTINATIONS, not the words: two of the labels are i18n keys,
    // and it is the reachable set that had drifted.
    const hrefs = within(screen.getByRole("menu"))
      .getAllByRole("menuitem")
      .map((el) => el.getAttribute("href"));
    expect(hrefs).toEqual(
      expect.arrayContaining(["/kb", "/review", "/diagnostics", "/my-resources", "/help"]),
    );
  });

  it("calls an item what the App calls it, not a chat (#pm)", () => {
    // The rail lists ITEMS. Where an item holds many conversations — a PM
    // project does — calling it a "chat" names the wrong level, and the
    // manifest already declares the right word.
    renderRail();

    expect(screen.getByRole("button", { name: /Start a Project/i })).toBeInTheDocument();
    // Deliberately unequal to the "New <noun>" fallback, so this asserts the
    // manifest is READ rather than that the fallback happens to match.
    expect(screen.getByRole("tab", { name: /My Projects/i })).toBeInTheDocument();
    expect(screen.queryByText(/New chat/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/My chats/i)).not.toBeInTheDocument();
  });

  it("names an item by the App's noun in the accessible copy too (#pm)", () => {
    // The visible strings were converted first; these were missed because
    // nothing reads them on screen — a screen-reader user would still have been
    // told "chats".
    renderRail();

    expect(screen.getByRole("navigation", { name: "projects" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Project options for Oven drift/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Chat options/i })).not.toBeInTheDocument();
  });

  it("lists the app's chats, links each to its item (slash id encoded), marks the current one", () => {
    renderRail();
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
    const other = screen.getByRole("link", { name: /Sensor noise/ });
    expect(other).toHaveAttribute("href", "/a/rca/rca-investigation%2F2");
    const current = screen.getByRole("link", { name: /Oven drift/ });
    expect(current).toHaveAttribute("data-active", "true");
  });

  it("creates a chat with defaults when New chat is pressed (no create form)", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Start a Project/i }));
    expect(newChat).toHaveBeenCalled();
  });

  it("deletes a chat from its ⋯ menu, after a confirm", () => {
    window.confirm = vi.fn(() => true); // happy-dom has no confirm — stub it
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Project options for Oven drift/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(chatActions.remove).toHaveBeenCalledWith("rca-investigation/1");
  });

  it("renames a chat inline from its ⋯ menu", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Project options for Oven drift/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: /Rename Project/i });
    fireEvent.change(input, { target: { value: "Oven drift RCA" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(chatActions.rename).toHaveBeenCalledWith("rca-investigation/1", "Oven drift RCA");
  });

  it("separates my chats from ones shared with me into tabs", () => {
    renderRail();
    // default "My chats" tab: my chats show, shared-with-me hidden
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
    expect(screen.queryByText("From a teammate")).not.toBeInTheDocument();
    // "Shared with me" tab: the teammate's chat, with no owner-only ⋯ actions
    fireEvent.click(screen.getByRole("tab", { name: /Shared with me/i }));
    expect(screen.getByText("From a teammate")).toBeInTheDocument();
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
    const row = screen.getByText("From a teammate").closest(".chat-rail__row") as HTMLElement;
    expect(within(row).queryByRole("button", { name: /Project options/i })).not.toBeInTheDocument();
  });

  // A chat I didn't make shows up in my rail with only its title — nothing said
  // where it came from, so "From a teammate" read like a chat I'd forgotten
  // writing. The one thing that distinguishes it is who put it there.
  it("names who shared each chat in the Shared with me tab", () => {
    renderRail();
    fireEvent.click(screen.getByRole("tab", { name: /Shared with me/i }));
    const row = screen.getByText("From a teammate").closest(".chat-rail__row") as HTMLElement;
    expect(within(row).getByText(/Shared by/i)).toHaveTextContent("Sam Teammate");
  });

  it("says nothing about sharing on my own chats", () => {
    renderRail();
    const row = screen.getByText("Oven drift").closest(".chat-rail__row") as HTMLElement;
    expect(within(row).queryByText(/Shared by/i)).not.toBeInTheDocument();
  });

  /**
   * The tab used to be free-floating `useState("mine")` in a component that is
   * destroyed on every navigation — `AppWorkspaceInner` renders "Loading…"
   * instead of the workspace (rail included) whenever the item query hasn't
   * answered yet, which is every first visit to a chat. So opening a chat from
   * "Shared with me" landed you back on "My chats", with the chat you are
   * actually reading nowhere in the list. Derive the tab from the chat you're
   * in and it survives the remount — and a reload — for free.
   */
  it("opens on Shared with me when the chat you're in is one shared with you", () => {
    renderRail("rca-investigation/9");
    expect(screen.getByRole("tab", { name: /Shared with me/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("From a teammate")).toBeInTheDocument();
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
  });

  it("still lets you cross to the other tab from a shared chat", () => {
    renderRail("rca-investigation/9");
    fireEvent.click(screen.getByRole("tab", { name: /My Projects/i }));
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
    expect(screen.queryByText("From a teammate")).not.toBeInTheDocument();
  });

  it("opens the share dialog from a chat's ⋯ menu", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Project options for Oven drift/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Share" }));
    expect(screen.getByTestId("share-dialog")).toBeInTheDocument();
  });

  it("collapses to a thin bar and re-expands", () => {
    renderRail();
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /collapse projects/i }));
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show projects/i }));
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
  });

  it("tucks the platform overview behind a menu button (Apps + KB / Review / …)", () => {
    renderRail();
    // hidden until the button is pressed — keeps the chat surface clean
    expect(screen.queryByRole("menuitem", { name: "Knowledge base" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /platform menu/i }));
    expect(screen.getByRole("menuitem", { name: "Knowledge base" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Product" })).toHaveAttribute("href", "/a/pm");
  });
});

/**
 * #fe-responsive — the rail is a hard 240px column with no responsive rule of
 * its own. Measured in a real browser at 390x844 it still took 240px, leaving
 * 150px for the entire chat surface: the composer, model picker, todo panel
 * and agent header were all cut off at the right edge with no scrollbar.
 *
 * Narrow starts it tucked, and an expanded rail overlays the chat rather than
 * taking a bite out of it — the same treatment the shell's file-tree sidebar
 * already gets below the breakpoint.
 */
describe("ChatListRail on a narrow viewport (#fe-responsive)", () => {
  const setViewport = stubViewport;

  it("starts tucked when the rail would leave the shell too little room", () => {
    // The rail is not a passenger: it sits BESIDE the shell, so below
    // shell + rail the open rail is the very thing that would force the shell
    // into its single-column layout. It tucks itself instead.
    setViewport(BREAKPOINTS.shell + BREAKPOINTS.chatRail - 1);
    renderRail();
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /show projects/i })).toBeInTheDocument();
  });

  it("stays tucked on a phone", () => {
    setViewport(390);
    renderRail();
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
  });

  it("starts open once there is room for the rail AND the shell's columns", () => {
    setViewport(BREAKPOINTS.shell + BREAKPOINTS.chatRail);
    renderRail();
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
  });

  it("can still be opened when tucked — tucked is a default, not a lockout", () => {
    setViewport(390);
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /show projects/i }));
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
  });
});

/**
 * The overlay rail is a DRAWER, and a drawer you cannot dismiss by looking away
 * is a trap: below 768px it sits ON TOP of the chat's own toolbar, so the chat
 * switcher and everything else on the bar's left edge stop responding — the
 * clicks land on the rail. Measured in Chromium at 760px wide: reopening the
 * rail moves the switcher's trigger to x=8 under a rail spanning 0–240, and a
 * click there resolves to `chat-rail__tab` while the switcher's menu never
 * opens. Collapsing the rail by hand was the only way back to it.
 *
 * Only where the rail actually overlays. Above the breakpoint it holds its own
 * column and nothing is covered, so dismissing it on a stray click would be its
 * own bug — a rail that will not stay open.
 */
describe("ChatListRail as a drawer (#pm)", () => {
  const openDrawer = () => fireEvent.click(screen.getByRole("button", { name: /show projects/i }));
  const isOpen = () => screen.queryByText("Oven drift") !== null;

  it("closes when you click away from it", () => {
    stubViewport(390);
    renderRail();
    openDrawer();
    expect(isOpen()).toBe(true);

    fireEvent.click(screen.getByTestId("chat-rail-scrim"));
    expect(isOpen()).toBe(false);
  });

  it("closes on Escape", () => {
    stubViewport(390);
    renderRail();
    openDrawer();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(isOpen()).toBe(false);
  });

  it("closes once you pick something — it would cover what you just opened", () => {
    stubViewport(390);
    renderRail();
    openDrawer();

    fireEvent.click(screen.getByRole("link", { name: /Sensor noise/ }));
    expect(isOpen()).toBe(false);
  });

  it("does none of that when the rail has its own column", () => {
    stubViewport(BREAKPOINTS.shell + BREAKPOINTS.chatRail);
    renderRail();
    expect(isOpen()).toBe(true);
    expect(screen.queryByTestId("chat-rail-scrim")).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("link", { name: /Sensor noise/ }));
    expect(isOpen()).toBe(true);
  });
});
