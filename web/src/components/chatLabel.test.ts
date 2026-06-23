import { describe, expect, it } from "vitest";

import type { ItemChatSummary } from "../api/itemChats";
import { chatLabel } from "./chatLabel";

const chat = (over: Partial<ItemChatSummary>): ItemChatSummary => ({
  chat_id: "c",
  title: "",
  run_id: null,
  created_ms: null,
  message_count: 0,
  is_default: false,
  name_hint: "",
  status: null,
  last_activity_ms: null,
  ...over,
});

describe("chatLabel", () => {
  it("prefers the explicit title", () => {
    expect(chatLabel(chat({ title: "Yield study", name_hint: "ignored" }))).toBe("Yield study");
  });

  it("falls back to the first-message hint when unnamed", () => {
    expect(chatLabel(chat({ name_hint: "Compare Q3 and Q4 yield" }))).toBe("Compare Q3 and Q4 yield");
  });

  it("shows 'New chat' for an unnamed, message-less chat — no default-chat privilege", () => {
    expect(chatLabel(chat({}))).toBe("New chat");
    expect(chatLabel(chat({ is_default: true }))).toBe("New chat");
  });
});
