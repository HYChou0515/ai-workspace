// @vitest-environment happy-dom
import { readFile } from "node:fs/promises";

import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { itemChatApi } from "./itemChats";

afterEach(() => vi.unstubAllGlobals());

function respondWith(status: number, body = "") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status })),
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
  /**
   * The claim `httpErrorFrom` was introduced to make is "every throw in this
   * file carries the code", and behaviour tests can only pin the throws they
   * exercise — five of the six could be reverted to `new HttpError(...)` with
   * the whole suite still green, which is exactly how the original asymmetry
   * survived. So the shape is asserted directly: the point of moving the rule
   * into a helper is that the next call site cannot forget it.
   */
  it("builds every error through the shared constructor, so none can drop the code", async () => {
    // Vitest runs with `web/` as cwd; `import.meta.url` is not a file: URL here.
    const source = await readFile("src/api/itemChats.ts", "utf8");
    expect(source).not.toMatch(/new HttpError\(/);
    expect(source).toMatch(/httpErrorFrom\(/);
  });

  it("throws a status-carrying HttpError from sendMessage", async () => {
    respondWith(504);
    await expect(
      itemChatApi.sendMessage({ slug: "rca", itemId: "INC-1", chatId: "c1", content: "hi" }),
    ).rejects.toMatchObject({ status: 504 });
  });

  /**
   * Reading the error body must not be able to swallow the rejection.
   *
   * The old throws fired at the response HEADERS and never touched the body.
   * Routing them through `errorCode` made every one of them wait for the body
   * to finish — and a 5xx whose body is delivered but never closed (an ingress
   * cutting a stream mid-flight) then rejects NEVER instead of in milliseconds.
   *
   * On `subscribe` that is the worst shape available: the chat's reconnect loop
   * only exits on unmount, so a first `next()` that never settles leaves the
   * chat permanently deaf. On a send it leaves the composer locked forever; on
   * a mutation it never reaches the write-failure notice, which is the one
   * place that promises no save fails in silence.
   */
  it("rejects at the status even when the error body never finishes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(new TextEncoder().encode('{"detail":'));
                // …and never closes.
              },
            }),
            { status: 502 },
          ),
      ),
    );
    const settled = await Promise.race([
      itemChatApi
        .sendMessage({ slug: "rca", itemId: "INC-1", chatId: "c1", content: "hi" })
        .then(
          () => "resolved",
          (e: unknown) => e,
        ),
      new Promise((resolve) => setTimeout(() => resolve("HUNG"), 3000)),
    ]);
    expect(settled).toMatchObject({ status: 502 });
  });

  /**
   * The same asymmetry one level down, found the same way (#714).
   *
   * The status was fixed here once; the machine-readable REASON was not, and it
   * is the half that carries meaning when the status cannot. A refusal the
   * backend explains as `request_env_failed` arrives as plain 500, and 500 has
   * no fallback the way 507 does — so the composer rendered "send failed: 500",
   * the exact string the feature existed to replace. This asserts at the
   * transport, because a hook test can always hand itself a `code` no client
   * produces.
   */
  it("carries the backend's machine-readable reason out of sendMessage", async () => {
    respondWith(500, JSON.stringify({ detail: { error: "request_env_failed" } }));
    await expect(
      itemChatApi.sendMessage({ slug: "rca", itemId: "INC-1", chatId: "c1", content: "hi" }),
    ).rejects.toMatchObject({ status: 500, code: "request_env_failed" });
  });

  /**
   * The inputs the hand-written throws already handled, kept working after they
   * were routed through `httpErrorFrom`: an empty or non-JSON body must still
   * produce the same status and message, with `code` simply absent. `errorCode`
   * clones before reading and swallows a parse failure, so no caller loses the
   * wording it wrote.
   */
  it("still reports status and message when the body carries no reason", async () => {
    respondWith(504, "gateway timeout, in plain text");
    await expect(
      itemChatApi.sendMessage({ slug: "rca", itemId: "INC-1", chatId: "c1", content: "hi" }),
    ).rejects.toMatchObject({ status: 504, message: "send failed: 504", code: undefined });
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

/**
 * The workspace's chat runs through THIS client, not `real.ts`. Setting a body
 * field on `real.ts` alone leaves it plumbed everywhere except on the wire —
 * which is how `answers` first shipped: typed end to end, dropped at the POST.
 */
describe("itemChatApi body fields", () => {
  it("sends the question an answer answers", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: { body?: BodyInit | null }) =>
        new Response("", { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await itemChatApi.sendMessage({
      slug: "rca",
      itemId: "INC-1",
      chatId: "c1",
      content: "SQLite",
      answers: "call_1",
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ content: "SQLite", answers: "call_1" });
  });

  it("sends the composer's kb + wiki search picks (#537 follow-up)", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: { body?: BodyInit | null }) =>
        new Response("", { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await itemChatApi.sendMessage({
      slug: "rca",
      itemId: "INC-1",
      chatId: "c1",
      content: "q",
      maxKbSearches: 2,
      maxWikiSearches: 1,
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.max_kb_searches).toBe(2);
    expect(body.max_wiki_searches).toBe(1);
  });
});
