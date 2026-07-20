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
