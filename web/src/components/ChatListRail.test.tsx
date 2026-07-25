// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatListRail } from "./ChatListRail";

const items = [
  { resource_id: "rca-investigation/1", title: "Oven drift", owner: "u" },
  { resource_id: "rca-investigation/2", title: "Sensor noise", owner: "u" },
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
