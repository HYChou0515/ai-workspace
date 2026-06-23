// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemChatSummary } from "../api/itemChats";
import { ChatSwitcher } from "./ChatSwitcher";

afterEach(cleanup);

const chat = (over: Partial<ItemChatSummary>): ItemChatSummary => ({
  chat_id: "c",
  title: "",
  run_id: null,
  created_ms: null,
  message_count: 0,
  is_default: false,
  name_hint: "",
  status: null,
  last_activity_ms: 1_700_000_000_000,
  ...over,
});

const chats = [
  chat({ chat_id: "c1", name_hint: "Compare Q3 and Q4" }),
  chat({ chat_id: "c2", title: "→memory", run_id: "r1", status: "awaiting_human" }),
];

describe("ChatSwitcher", () => {
  it("shows the active chat's label on the trigger", () => {
    render(<ChatSwitcher chats={chats} activeChatId="c2" onSelect={() => {}} onManage={() => {}} />);
    expect(screen.getByTestId("chat-switcher-trigger")).toHaveTextContent("→memory");
  });

  it("opens, lists every chat, and selects one on click (then closes)", () => {
    const onSelect = vi.fn();
    render(<ChatSwitcher chats={chats} activeChatId="c1" onSelect={onSelect} onManage={() => {}} />);
    fireEvent.click(screen.getByTestId("chat-switcher-trigger"));
    expect(screen.getByTestId("chat-switcher-item-c1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("chat-switcher-item-c2"));
    expect(onSelect).toHaveBeenCalledWith("c2");
    expect(screen.queryByTestId("chat-switcher-menu")).not.toBeInTheDocument();
  });

  it("shows a status badge on a workflow chat row", () => {
    render(<ChatSwitcher chats={chats} activeChatId="c1" onSelect={() => {}} onManage={() => {}} />);
    fireEvent.click(screen.getByTestId("chat-switcher-trigger"));
    expect(screen.getByTestId("chat-switcher-item-c2")).toHaveTextContent("awaiting");
  });

  it("opens the manage modal from the footer item", () => {
    const onManage = vi.fn();
    render(<ChatSwitcher chats={chats} activeChatId="c1" onSelect={() => {}} onManage={onManage} />);
    fireEvent.click(screen.getByTestId("chat-switcher-trigger"));
    fireEvent.click(screen.getByTestId("chat-switcher-manage"));
    expect(onManage).toHaveBeenCalledTimes(1);
  });
});
