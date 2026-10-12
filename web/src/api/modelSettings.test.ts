// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { modelSettingsApi, problemsOf } from "./modelSettings";

afterEach(() => vi.unstubAllGlobals());

describe("modelSettingsApi.apply", () => {
  it("says a 409 in the server's own sentence, as no problem in the draft", async () => {
    // final review 5: the 409 came back as a "problem", which blocked Apply
    // until the draft was edited — there was nothing in it to fix.
    const sentence = "the draft or the keys changed since this page read them — reload";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: sentence }), { status: 409 })),
    );

    const failed = await modelSettingsApi.apply({}, [], "rev-1").catch((e: unknown) => e);

    expect(failed).toBeInstanceOf(HttpError);
    expect((failed as HttpError).message).toBe(sentence);
    expect(problemsOf(failed)).toEqual([]);
  });

  it("sends what the confirmation listed, the key revision included", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ version: "v", patch: {}, by: "r", applied_at: 1, previous: null })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await modelSettingsApi.apply({ config: {} }, ["config.llm.api_key"], "rev-9");

    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({
      draft: { config: {} },
      pending_keys: ["config.llm.api_key"],
      key_revision: "rev-9",
    });
  });
});
