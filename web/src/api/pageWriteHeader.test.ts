// @vitest-environment happy-dom
/** docs/plan-wui-user.md D12: a page's write names the page in a header the
 * server reads; nothing else sends it, which is what refuses a WUI User's write
 * from the file tree. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi, WUI_PAGE_HEADER } from "./real";

function fetchSpy() {
  const sent: { url: string; headers: Headers }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      sent.push({ url: String(input), headers: new Headers(init?.headers) });
      return new Response(null, { status: 204 });
    }),
  );
  return sent;
}

describe("the page header on file writes", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("is sent with a page's write and delete", async () => {
    const sent = fetchSpy();
    await realApi.writeFile("rca", "inv1", "/t/a.json", "{}", { page: "/t/p.ai.yaml" });
    await realApi.deleteFile("rca", "inv1", "/t/a.json", { page: "/t/p.ai.yaml" });
    // percent-encoded on the wire (a header value must be Latin-1)
    expect(sent.map((s) => decodeURIComponent(s.headers.get(WUI_PAGE_HEADER) ?? ""))).toEqual([
      "/t/p.ai.yaml",
      "/t/p.ai.yaml",
    ]);
  });

  it("is absent from every other write", async () => {
    const sent = fetchSpy();
    await realApi.writeFile("rca", "inv1", "/t/a.json", "{}");
    await realApi.deleteFile("rca", "inv1", "/t/a.json");
    expect(sent.map((s) => s.headers.get(WUI_PAGE_HEADER))).toEqual([null, null]);
  });
});

describe("a refused page write keeps the server's sentence (docs/plan-wui-user.md D12)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("for a write and a delete, with the code", async () => {
    const sentence = "this page may not change /sales/page.ai.yaml: a page writes only inside its own folder, never its view file";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: sentence }), { status: 403 })),
    );
    const write = await realApi
      .writeFile("rca", "inv1", "/sales/page.ai.yaml", "x", { page: "/sales/page.ai.yaml" })
      .catch((e: unknown) => e);
    const del = await realApi
      .deleteFile("rca", "inv1", "/sales/page.ai.yaml", { page: "/sales/page.ai.yaml" })
      .catch((e: unknown) => e);
    for (const err of [write, del]) expect(String((err as Error).message)).toContain("never its view file");
  });
});
