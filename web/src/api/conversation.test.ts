import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

// #139: the backend `Conversation` struct serializes its owning-item handle as
// `item_id` (renamed from the old `investigation_id`). `getConversation`
// hydrates the shared RCA chat by listing `/conversation` and matching that
// field. If the FE reads the wrong key it matches nothing → returns null →
// the workspace chat history (everyone's, not just other users') never loads.
describe("realApi.getConversation — #139 wire field is item_id", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("finds the conversation by the backend `item_id` field and returns its messages", async () => {
    const wire = [
      {
        data: {
          item_id: "rca-investigation:abc",
          messages: [
            { role: "user", content: "hi from alice", author: "alice" },
            { role: "assistant", content: "hello", author: "RCA Agent" },
          ],
        },
        revision_info: { resource_id: "conversation:1" },
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(wire), { status: 200 })),
    );

    const conv = await realApi.getConversation("rca-investigation:abc");

    expect(conv).not.toBeNull();
    expect(conv?.messages.map((m) => m.content)).toEqual(["hi from alice", "hello"]);
  });
});
