/**
 * The client, not the page. `MyResourcesPage.test.tsx` injects a fake
 * `MyResourcesApi`, so the real module here was never run by anything: the whole
 * behaviour change this feature turns on — a refusal no longer resolving as
 * success — had zero coverage across all 2934 web tests, and deleting the check
 * left every one of them green.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { myResourcesApi } from "./myResources";

afterEach(() => {
  vi.unstubAllGlobals();
});

function respondWith(status: number, body: unknown = {}): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
}

describe("closeEnvironment", () => {
  it("resolves when the environment really was closed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(myResourcesApi.closeEnvironment("i-1")).resolves.toBeUndefined();
  });

  it("rejects when the backend refuses, rather than reporting success", async () => {
    // 503 is the real case: a reachable-but-slow host cannot be shut down right
    // now and every record is deliberately left in place. Swallowing it made the
    // page refetch and tell the person it had worked.
    respondWith(503, { detail: { error: "sandbox_busy" } });
    await expect(myResourcesApi.closeEnvironment("i-1")).rejects.toBeInstanceOf(HttpError);
  });

  it("carries the status and the code, so the caller can say which refusal it was", async () => {
    respondWith(503, { detail: { error: "sandbox_busy" } });
    const err = await myResourcesApi
      .closeEnvironment("i-1")
      .then(() => null)
      .catch((e: unknown) => e as HttpError);
    expect(err).toBeInstanceOf(HttpError);
    expect(err?.status).toBe(503);
    expect(err?.code).toBe("sandbox_busy");
  });

  it("encodes the item id, which contains a colon", async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        seen.push(url);
        return new Response(null, { status: 204 });
      }),
    );
    await myResourcesApi.closeEnvironment("rca-investigation:ab/cd");
    expect(seen[0]).toContain("rca-investigation%3Aab%2Fcd");
  });
});
