// @vitest-environment node
/** Node's `Headers`, like every browser's, refuses a value that is not Latin-1
 * (happy-dom's does not, so this runs under node). A page's view path can hold
 * any character — a Chinese folder name — so it is sent percent-encoded
 * (docs/plan-wui-user.md D12). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi, WUI_PAGE_HEADER } from "./real";

describe("a page whose path is not Latin-1", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is sent encoded, since a header value cannot hold it raw", async () => {
    const sent: Headers[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        sent.push(new Headers(init?.headers));
        return new Response(null, { status: 204 });
      }),
    );
    await realApi.writeFile("rca", "inv1", "/報表/a.json", "{}", { page: "/報表/page.ai.yaml" });
    await realApi.deleteFile("rca", "inv1", "/報表/a.json", { page: "/報表/page.ai.yaml" });
    expect(sent.map((h) => decodeURIComponent(h.get(WUI_PAGE_HEADER) ?? ""))).toEqual([
      "/報表/page.ai.yaml",
      "/報表/page.ai.yaml",
    ]);
  });
});
