// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
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

  it("labels an unnamed thread by its first user message, not a generic 'Chat' (#357)", async () => {
    const unnamed: KbChatDetail = {
      ...baseChat,
      title: "",
      name_hint: "why is my reflow oven drifting",
    };
    render(<KbChatView chatId="chat:1" client={chatClient(unnamed)} />);
    expect(await screen.findByText("why is my reflow oven drifting")).toBeInTheDocument();
    expect(screen.queryByText("Chat")).not.toBeInTheDocument();
  });

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

  it("signals the pinned state visually (aria-pressed + active fill), not only via the label (#466)", async () => {
    // Unique id: pin state persists in localStorage across tests, so isolate from
    // the "pins the conversation" test above (which leaves chat:1 pinned).
    const fresh: KbChatDetail = { ...baseChat, resource_id: "chat:pinviz" };
    render(<KbChatView chatId="chat:pinviz" client={chatClient(fresh)} />);
    await screen.findByText("Void thresholds");
    const pin = screen.getByRole("button", { name: "Pin conversation" });
    expect(pin).toHaveAttribute("aria-pressed", "false");
    expect(pin.className).not.toContain("kb-btn--on");
    await userEvent.click(pin);
    const unpin = screen.getByRole("button", { name: "Unpin conversation" });
    expect(unpin).toHaveAttribute("aria-pressed", "true");
    expect(unpin.className).toContain("kb-btn--on"); // accent active state, not just text
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

// The Share affordance mirrors the backend gate for POST /kb/chats/{id}/share
// (change_permission): owner OR superuser. It was `owner === me` with an
// `owner ?? me` fallback — hiding it from admins AND treating an owner-less
// row as "mine".
describe("KbChatView share gate", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers Share to a superuser who is not the owner", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ id: "default-user", is_superuser: true, groups: [] });
    const chat = { ...baseChat, owner: "someone-else" };
    render(<KbChatView chatId="chat:1" client={chatClient(chat)} />);
    await screen.findByText("Void thresholds");
    expect(screen.getByRole("button", { name: /Share/ })).toBeInTheDocument();
  });

  it("hides Share from a plain non-owner reading a shared chat", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ id: "default-user", is_superuser: false, groups: [] });
    const chat = { ...baseChat, owner: "someone-else", shared_with: ["default-user"] };
    render(<KbChatView chatId="chat:1" client={chatClient(chat)} />);
    await screen.findByText("Void thresholds");
    expect(screen.queryByRole("button", { name: /Share/ })).not.toBeInTheDocument();
  });

  it("does NOT treat an owner-less chat as mine (unknown ≠ mine)", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ id: "default-user", is_superuser: false, groups: [] });
    const chat = { ...baseChat, owner: undefined as unknown as string };
    render(<KbChatView chatId="chat:1" client={chatClient(chat)} />);
    await screen.findByText("Void thresholds");
    expect(screen.queryByRole("button", { name: /Share/ })).not.toBeInTheDocument();
  });
});
