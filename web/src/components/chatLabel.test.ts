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

  it("labels an unnamed, message-less chat by its creation time", () => {
    const created_ms = new Date(2026, 5, 24, 14, 32).getTime(); // local June 24, 14:32
    expect(chatLabel(chat({ created_ms }))).toBe("Chat · 6/24 14:32");
  });

  it("zero-pads the minute but not the month/day/hour", () => {
    const created_ms = new Date(2026, 0, 3, 9, 5).getTime(); // local Jan 3, 09:05
    expect(chatLabel(chat({ created_ms }))).toBe("Chat · 1/3 9:05");
  });

  it("grants the default chat no special label — still a creation-time label", () => {
    const created_ms = new Date(2026, 5, 24, 14, 32).getTime();
    expect(chatLabel(chat({ is_default: true, created_ms }))).toBe("Chat · 6/24 14:32");
  });

  it("falls back to a plain 'Chat' when the creation time is missing", () => {
    expect(chatLabel(chat({ created_ms: null }))).toBe("Chat");
  });
});
