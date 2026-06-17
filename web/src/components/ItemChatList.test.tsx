// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemChatSummary } from "../api/itemChats";
import { ItemChatList, chatLabel } from "./ItemChatList";

afterEach(cleanup);

const chat = (over: Partial<ItemChatSummary>): ItemChatSummary => ({
  chat_id: "c",
  title: "",
  run_id: null,
  created_ms: null,
  message_count: 0,
  is_default: false,
  ...over,
});

describe("chatLabel", () => {
  it("prefers the title, else labels default/free/workflow chats", () => {
    expect(chatLabel(chat({ title: "Onboarding" }))).toBe("Onboarding");
    expect(chatLabel(chat({ is_default: true }))).toBe("Chat");
    expect(chatLabel(chat({}))).toBe("Free chat");
    expect(chatLabel(chat({ run_id: "run-1" }))).toBe("Workflow");
  });
});

describe("ItemChatList", () => {
  it("renders a tab per chat and marks the active one", () => {
    const chats = [
      chat({ chat_id: "c1", is_default: true }),
      chat({ chat_id: "c2", title: "Heat run", run_id: "run-1" }),
    ];
    render(<ItemChatList chats={chats} activeChatId="c2" onSelect={() => {}} />);
    expect(screen.getByTestId("chat-tab-c1")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("chat-tab-c2")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("chat-tab-c2")).toHaveTextContent("Heat run");
  });

  it("selects a chat on click", () => {
    const onSelect = vi.fn();
    render(<ItemChatList chats={[chat({ chat_id: "c1" })]} activeChatId={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("chat-tab-c1"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });
});
