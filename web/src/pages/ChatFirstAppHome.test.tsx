// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppManifest } from "../api/types";
import { ChatFirstAppHome } from "./ChatFirstAppHome";

const itemsRef: { current: { resource_id: string; title: string }[] } = { current: [] };
vi.mock("../hooks/useResources", () => ({
  useAppItems: () => ({ items: itemsRef.current, isPending: false }),
}));
vi.mock("../components/ChatListRail", () => ({ ChatListRail: () => <div data-testid="rail" /> }));
const newChat = vi.fn();
vi.mock("../hooks/useCreateChat", () => ({ useCreateChat: () => ({ mutate: newChat, isPending: false }) }));

const mf = {
  resource_route: "/playground-item",
  layout: { primary_surface: "chat" },
} as unknown as AppManifest;

afterEach(() => {
  cleanup();
  localStorage.clear();
  itemsRef.current = [];
});

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/a/:slug" element={<ChatFirstAppHome slug="playground" manifest={mf} />}>
          <Route path="new" element={<div data-testid="new-modal" />} />
        </Route>
        <Route path="/a/:slug/:itemId" element={<div data-testid="workspace" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ChatFirstAppHome", () => {
  it("resumes the App's remembered chat when it still exists", () => {
    itemsRef.current = [
      { resource_id: "playground-item:2", title: "b" },
      { resource_id: "playground-item:1", title: "a" },
    ];
    localStorage.setItem("chat:last:playground", "playground-item:1");
    renderAt("/a/playground");
    expect(screen.getByTestId("workspace")).toBeInTheDocument();
  });

  it("falls back to the newest chat when nothing is remembered", () => {
    itemsRef.current = [{ resource_id: "playground-item:2", title: "b" }];
    renderAt("/a/playground");
    expect(screen.getByTestId("workspace")).toBeInTheDocument();
  });

  it("shows a start-new-chat empty state that creates a chat (no create form)", () => {
    itemsRef.current = [];
    renderAt("/a/playground");
    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(newChat).toHaveBeenCalled();
  });

  it("does not redirect while creating a new chat, so the create form can show", () => {
    itemsRef.current = [{ resource_id: "playground-item:2", title: "b" }];
    renderAt("/a/playground/new");
    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();
    expect(screen.getByTestId("new-modal")).toBeInTheDocument();
  });
});
