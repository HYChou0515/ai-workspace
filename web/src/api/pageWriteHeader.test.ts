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
