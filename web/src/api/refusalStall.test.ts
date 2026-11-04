/** A refusal's sentence is read from the body `errorInfo` already read, under its
 * deadline — never by reading the response a second time. A second, unbounded
 * read hung a page's save (and every editor save) on an error body that never
 * finished: the same trap `execShell.test.ts` pins (review round 3, M1). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { pageRefusal } from "../renderers/wui/api";
import { realApi } from "./real";

function stalling(status: number): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"detail":'));
        // …and never closes.
      },
    }),
    { status },
  );
}

const within = <T,>(p: Promise<T>, ms: number) =>
  Promise.race([p.then(() => "SETTLED", () => "SETTLED"), new Promise((r) => setTimeout(() => r("HUNG"), ms))]);

describe("a refusal whose body never finishes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("still settles a write, a delete and a page call", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => stalling(502)));
    expect(await within(realApi.writeFile("rca", "i1", "/a.json", "{}"), 4000)).toBe("SETTLED");
    expect(await within(realApi.deleteFile("rca", "i1", "/a.json"), 4000)).toBe("SETTLED");
    expect(await within(pageRefusal(stalling(403), "x"), 4000)).toBe("SETTLED");
  }, 15000);
});
