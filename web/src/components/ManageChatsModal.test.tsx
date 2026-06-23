// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemChatSummary } from "../api/itemChats";
import { ManageChatsModal } from "./ManageChatsModal";

afterEach(cleanup);

const chat = (over: Partial<ItemChatSummary>): ItemChatSummary => ({
  chat_id: "c",
  title: "",
  run_id: null,
  created_ms: null,
  message_count: 3,
  is_default: false,
  name_hint: "",
  status: null,
  last_activity_ms: 1_700_000_000_000,
  ...over,
});

const chats = [
  chat({ chat_id: "c1", name_hint: "Compare Q3 and Q4" }),
  chat({ chat_id: "c2", title: "Memory digest", run_id: "r1", status: "running" }),
];

const props = () => ({
  chats,
  activeChatId: "c1" as string | null,
  onClose: vi.fn(),
  onSelect: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
});

describe("ManageChatsModal", () => {
  it("lists a row per chat with its workflow status", () => {
    render(<ManageChatsModal {...props()} />);
    expect(screen.getByTestId("manage-chat-row-c1")).toHaveTextContent("Compare Q3 and Q4");
    expect(screen.getByTestId("manage-chat-row-c2")).toHaveTextContent("running");
  });

  it("filters rows by the search box", () => {
    render(<ManageChatsModal {...props()} />);
    fireEvent.change(screen.getByTestId("manage-chats-search"), { target: { value: "memory" } });
    expect(screen.queryByTestId("manage-chat-row-c1")).not.toBeInTheDocument();
    expect(screen.getByTestId("manage-chat-row-c2")).toBeInTheDocument();
  });

  it("switches to a chat and closes via the switch button", () => {
    const p = props();
    render(<ManageChatsModal {...p} />);
    fireEvent.click(screen.getByTestId("manage-switch-c2"));
    expect(p.onSelect).toHaveBeenCalledWith("c2");
    expect(p.onClose).toHaveBeenCalled();
  });

  it("renames a chat inline via the edit button", () => {
    const p = props();
    render(<ManageChatsModal {...p} />);
    fireEvent.click(screen.getByTestId("manage-edit-c1"));
    const input = screen.getByTestId("manage-rename-input-c1");
    fireEvent.change(input, { target: { value: "Yield study" } });
    fireEvent.click(screen.getByTestId("manage-rename-save-c1"));
    expect(p.onRename).toHaveBeenCalledWith("c1", "Yield study");
  });

  it("deletes a chat only after a confirm step", () => {
    const p = props();
    render(<ManageChatsModal {...p} />);
    fireEvent.click(screen.getByTestId("manage-delete-c2"));
    expect(p.onDelete).not.toHaveBeenCalled(); // first click only arms the confirm
    fireEvent.click(screen.getByTestId("manage-delete-confirm-c2"));
    expect(p.onDelete).toHaveBeenCalledWith("c2");
  });

  it("closes from the close button", () => {
    const p = props();
    render(<ManageChatsModal {...p} />);
    fireEvent.click(screen.getByTestId("manage-chats-close"));
    expect(p.onClose).toHaveBeenCalled();
  });
});
