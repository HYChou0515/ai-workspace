// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { itemChatApi } from "./itemChats";

afterEach(() => vi.unstubAllGlobals());

function respondWith(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("", { status })),
  );
}

/**
 * The WorkItem chat client and the RCA client (`api/real.ts`) talk to the SAME
 * turn engine, but only `real.ts` threw a status-carrying `HttpError`; this one
 * threw a bare `Error("send failed: 504")`.
 *
 * That asymmetry is not cosmetic. The send path treats 502/503/504 (and a bare
 * network drop) as "the gateway cut the request, but the turn is running" and
 * deliberately STAYS streaming so the stream / store-poll can surface the reply.
 * That branch reads `err.status` — so against a statusless error it can never
 * fire, and a WorkItem chat shows a hard red "send failed: 504" while the answer
 * streams in underneath it.
 */
describe("itemChatApi error contract", () => {
  it("throws a status-carrying HttpError from sendMessage", async () => {
    respondWith(504);
    await expect(
      itemChatApi.sendMessage({ slug: "rca", itemId: "INC-1", chatId: "c1", content: "hi" }),
    ).rejects.toMatchObject({ status: 504 });
  });

  it("throws a status-carrying HttpError from a JSON read", async () => {
    respondWith(403);
    await expect(itemChatApi.getChat("rca", "INC-1", "c1")).rejects.toBeInstanceOf(HttpError);
  });

  it("throws a status-carrying HttpError from the stream open", async () => {
    respondWith(502);
    await expect(async () => {
      for await (const _ of itemChatApi.subscribe("rca", "INC-1", "c1")) {
        // the stream never opens — the throw happens on the first pull
      }
    }).rejects.toMatchObject({ status: 502 });
  });
});

/** The remaining call sites. They were entirely untested — this client had no
 * test file at all — so a wrong path or verb would have shipped silently. */
describe("itemChatApi call sites", () => {
  function capture(body: string, status = 200) {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(body, {
          status,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    return calls;
  }

  it("reads a chat thread", async () => {
    const calls = capture(
      JSON.stringify({ data: { title: "T", run_id: null, messages: [] }, revision_info: { resource_id: "c1" } }),
    );
    const chat = await itemChatApi.getChat("rca", "INC-1", "c1");
    expect(chat.messages).toEqual([]);
    // A chat thread is read from the shared Conversation resource, NOT a
    // per-item sub-path — that shared store is what lets any pod serve it,
    // which is the whole basis of the cross-pod store-poll fallback.
    expect(calls[0]!.url).toContain("/conversation/c1");
  });

  it("undoes turns on the chat", async () => {
    const calls = capture("{}");
    await itemChatApi.undoTurns("rca", "INC-1", "c1", 2);
    expect(calls[0]!.init?.method).toBe("DELETE");
    expect(calls[0]!.url).toContain("/chats/c1/messages?turns=2");
  });

  it("mentions users on the ITEM, not the chat", async () => {
    // A mention is item-scoped: the item's collaborators are notified, not a
    // per-chat subset. Getting this wrong notifies nobody.
    const calls = capture("{}");
    await itemChatApi.mention("rca", "INC-1", ["bob"], "look");
    expect(calls[0]!.url).toContain("/items/INC-1/mentions");
    expect(calls[0]!.url).not.toContain("/chats/");
  });

  it("cancels the in-flight turn of the chat", async () => {
    const calls = capture("{}");
    await itemChatApi.cancelMessage("rca", "INC-1", "c1");
    expect(calls[0]!.init?.method).toBe("DELETE");
  });
});
