// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatListRail } from "./ChatListRail";

const items = [
  { resource_id: "rca-investigation/1", title: "Oven drift", owner: "me" },
  { resource_id: "rca-investigation/2", title: "Sensor noise", owner: "me" },
  { resource_id: "rca-investigation/9", title: "From a teammate", owner: "someone-else" },
];
vi.mock("../hooks/useResources", () => ({
  useAppItems: () => ({ items, isPending: false }),
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
vi.mock("./ShareChatDialog", () => ({ ShareChatDialog: () => <div data-testid="share-dialog" /> }));

afterEach(cleanup);

function renderRail() {
  return render(
    <MemoryRouter>
      <ChatListRail slug="rca" resourceRoute="/rca-investigation" currentId="rca-investigation/1" />
    </MemoryRouter>,
  );
}

describe("ChatListRail", () => {
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
    fireEvent.click(screen.getByRole("button", { name: /New chat/i }));
    expect(newChat).toHaveBeenCalled();
  });

  it("deletes a chat from its ⋯ menu, after a confirm", () => {
    window.confirm = vi.fn(() => true); // happy-dom has no confirm — stub it
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Chat options for Oven drift/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(chatActions.remove).toHaveBeenCalledWith("rca-investigation/1");
  });

  it("renames a chat inline from its ⋯ menu", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Chat options for Oven drift/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: /Rename chat/i });
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
    expect(within(row).queryByRole("button", { name: /Chat options/i })).not.toBeInTheDocument();
  });

  it("opens the share dialog from a chat's ⋯ menu", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /Chat options for Oven drift/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Share" }));
    expect(screen.getByTestId("share-dialog")).toBeInTheDocument();
  });

  it("collapses to a thin bar and re-expands", () => {
    renderRail();
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /collapse chats/i }));
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show chats/i }));
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
  const realMM = window.matchMedia;
  afterEach(() => {
    window.matchMedia = realMM;
  });
  function stubViewport(narrow: boolean) {
    window.matchMedia = ((q: string) => ({
      matches: narrow,
      media: q,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => true,
    })) as unknown as typeof window.matchMedia;
  }

  it("starts tucked on a narrow viewport so the chat keeps the width", () => {
    stubViewport(true);
    renderRail();
    expect(screen.queryByText("Oven drift")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /show chats/i })).toBeInTheDocument();
  });

  it("still starts open on a wide viewport", () => {
    stubViewport(false);
    renderRail();
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
  });

  it("can still be opened on narrow — tucked is a default, not a lockout", () => {
    stubViewport(true);
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: /show chats/i }));
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
  });
});
