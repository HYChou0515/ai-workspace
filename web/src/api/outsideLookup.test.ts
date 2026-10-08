// @vitest-environment happy-dom
/** The "請幫我查" card's calls (docs/plan-outside-lookup.md). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { outsideLookupApi, searchUrl } from "./outsideLookup";

afterEach(() => vi.unstubAllGlobals());

function capture(status = 200, body: unknown = { path: null, attachments: [] }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return new Response(JSON.stringify(body), { status });
    }),
  );
  return calls;
}

describe("searchUrl", () => {
  it("puts the query, encoded, wherever the target says", () => {
    expect(
      searchUrl({ name: "G", url: "https://g.example/s?q={q}&also={q}" }, "pandas 2.0 & 新功能"),
    ).toBe(
      "https://g.example/s?q=pandas%202.0%20%26%20%E6%96%B0%E5%8A%9F%E8%83%BD" +
        "&also=pandas%202.0%20%26%20%E6%96%B0%E5%8A%9F%E8%83%BD",
    );
  });
});

describe("outsideLookupApi.answer", () => {
  it("sends what was found, with the attachments, to the card's chat", async () => {
    const calls = capture(200, { path: "lookups/a.md", attachments: ["lookups/a/x.png"] });
    const file = new File(["x"], "x.png", { type: "image/png" });

    const saved = await outsideLookupApi.answer({
      slug: "rca",
      itemId: "i 1",
      chatId: "c/1",
      callId: "call_1",
      answer: { kind: "found", content: "md", sourceUrl: "https://s", target: "Google", attachments: [file] },
    });

    expect(saved).toEqual({ path: "lookups/a.md", attachments: ["lookups/a/x.png"] });
    expect(calls[0]!.url).toBe("/api/a/rca/items/i%201/chats/c%2F1/outside-answers");
    const form = calls[0]!.init!.body as FormData;
    expect(form.get("tool_call_id")).toBe("call_1");
    expect(form.get("kind")).toBe("found");
    expect(form.get("content")).toBe("md");
    expect(form.get("source_url")).toBe("https://s");
    expect(form.get("target")).toBe("Google");
    expect((form.getAll("attachments")[0] as File).name).toBe("x.png");
  });

  it("goes to the item's own route when the card is in its default chat", async () => {
    const calls = capture();

    await outsideLookupApi.answer({
      slug: "rca",
      itemId: "i",
      callId: "c",
      answer: { kind: "not_found", reason: "blocked" },
    });

    expect(calls[0]!.url).toBe("/api/a/rca/items/i/outside-answers");
    const form = calls[0]!.init!.body as FormData;
    expect(form.get("kind")).toBe("not_found");
    expect(form.get("reason")).toBe("blocked");
    expect(form.get("content")).toBeNull();
  });

  it("carries the status of a refusal", async () => {
    capture(507, { detail: "full" });

    const err = await outsideLookupApi
      .answer({ slug: "s", itemId: "i", callId: "c", answer: { kind: "not_found", reason: "" } })
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(507);
  });

  it("says the server's own reason when it gave one", async () => {
    capture(409, { detail: "this lookup card is already answered" });

    const err = await outsideLookupApi
      .answer({ slug: "s", itemId: "i", callId: "c", answer: { kind: "not_found", reason: "" } })
      .catch((e: unknown) => e);

    expect((err as HttpError).message).toBe("this lookup card is already answered");
    expect((err as HttpError).status).toBe(409);
  });
});
