/**
 * The PRIVATE env layer (`docs/plan-wui-viewer-login.md`): a person's own
 * values for one item. The routes address "mine" — no user in the URL — so the
 * client never names anybody.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { privateEnvApi } from "./privateEnv";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("privateEnvApi", () => {
  it("reads the caller's own values for the item", async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ values: { ERP_TOKEN: "t" } }));

    expect(await privateEnvApi.get("rca", "item 1")).toEqual({ ERP_TOKEN: "t" });
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/a/rca/items/item%201/env/private");
  });

  it("stores the whole set with PUT", async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ values: { A: "1" } }));

    await privateEnvApi.put("rca", "i1", { A: "1" });

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ values: { A: "1" } });
  });

  it("logs out with DELETE", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await privateEnvApi.clear("rca", "i1");

    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("surfaces a refusal instead of reading it as empty", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ detail: "no" }), { status: 403 }),
    );

    await expect(privateEnvApi.get("rca", "i1")).rejects.toThrow();
  });
});
