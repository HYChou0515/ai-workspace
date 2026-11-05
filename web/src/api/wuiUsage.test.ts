/** The deployed pages' report calls (docs/plan-wui-usage.md U8). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { wuiUsageApi } from "./wuiUsage";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown) {
  const spy = vi.fn(async () => new Response(JSON.stringify(body)));
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("wuiUsageApi", () => {
  it("asks for a window's report", async () => {
    const spy = stub({ days: 7, pages: [] });
    await wuiUsageApi.report(7);
    expect(String((spy.mock.calls[0] as unknown as [string])[0])).toBe("/api/admin/wui-usage?days=7");
  });

  it("names the page whose people it asks for, encoded", async () => {
    const spy = stub({ item_id: "i1", page: "/報表/page.ai.yaml", days: 30, people: [] });
    await wuiUsageApi.detail("i1", "/報表/page.ai.yaml", 30);
    const url = String((spy.mock.calls[0] as unknown as [string])[0]);
    expect(url).toBe(
      `/api/admin/wui-usage/detail?item_id=i1&page=${encodeURIComponent("/報表/page.ai.yaml")}&days=30`,
    );
  });

  it("raises the server's refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "superusers only" }), { status: 403 })));
    await expect(wuiUsageApi.report(30)).rejects.toThrow();
  });

  it("downloads the raw data from a link", () => {
    expect(wuiUsageApi.rawUrl(90)).toBe("/api/admin/wui-usage.csv?days=90");
  });
});
