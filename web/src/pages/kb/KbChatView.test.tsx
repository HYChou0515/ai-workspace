// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbChatDetail } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { KbChatView } from "./KbChatView";

function chatClient(chat: KbChatDetail): KbApi {
  return { ...mockKbApi, getChat: async () => structuredClone(chat) };
}

const baseChat: KbChatDetail = {
  resource_id: "chat:1",
  title: "Void thresholds",
  collection_ids: [],
  owner: "default-user",
  shared_with: [],
  messages: [
    { role: "user", content: "hi", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: Date.now(), citations: [] },
    { role: "assistant", content: "hello", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: Date.now(), citations: [] },
  ],
};

describe("KbChatView header", () => {
  afterEach(cleanup);

  it("shows the message count and a private badge", async () => {
    render(<KbChatView chatId="chat:1" client={chatClient(baseChat)} />);
    expect(await screen.findByText("Void thresholds")).toBeInTheDocument();
    expect(screen.getByText(/2 messages/)).toBeInTheDocument();
    expect(screen.getByText("private chat")).toBeInTheDocument();
  });

  it("pins the conversation (toggles the action label)", async () => {
    render(<KbChatView chatId="chat:1" client={chatClient(baseChat)} />);
    await screen.findByText("Void thresholds");
    await userEvent.click(screen.getByRole("button", { name: "Pin conversation" }));
    expect(screen.getByRole("button", { name: "Unpin conversation" })).toBeInTheDocument();
  });

  it("exports the thread as a re-ingestable .chat.json download", async () => {
    // Round-trip contract (issue #39 chat-history support): the export
    // must be DIRECTLY re-uploadable to a KB collection — `.chat.json`
    // suffix + the {title, messages:[{role, content, tool_name}]} shape
    // the BE's parse_chat_export validates. Raw KbChat dumps don't
    // round-trip.
    let exported: Blob | null = null;
    const createUrl = vi.spyOn(URL, "createObjectURL").mockImplementation((b) => {
      exported = b as Blob;
      return "blob:x";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    let filename = "";
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        filename = this.download;
      });
    render(<KbChatView chatId="chat:1" client={chatClient(baseChat)} />);
    await screen.findByText("Void thresholds");
    await userEvent.click(screen.getByRole("button", { name: /Export/ }));
    expect(createUrl).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(filename).toBe("Void-thresholds.chat.json");
    const body = JSON.parse(await exported!.text());
    expect(body).toEqual({
      title: "Void thresholds",
      messages: [
        { role: "user", content: "hi", tool_name: "" },
        { role: "assistant", content: "hello", tool_name: "" },
      ],
    });
  });
});
