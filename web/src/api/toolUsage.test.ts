// @vitest-environment happy-dom
/** The administrator's tool-usage report calls (plan-tool-usage-stats §3.6). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { toolUsageApi } from "./toolUsage";

afterEach(() => vi.unstubAllGlobals());

function capture(status = 200, body: unknown = { days: 30, tools: [] }) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL) => {
      urls.push(String(url));
      return new Response(JSON.stringify(body), { status });
    }),
  );
  return urls;
}

describe("toolUsageApi", () => {
  it("asks for the window and only the filters that are set", async () => {
    const urls = capture();

    await toolUsageApi.report(7, { source: "third_party", surface: "", app: "rca" });

    expect(urls[0]).toBe("/api/admin/tool-usage?days=7&source=third_party&app=rca");
  });

  it("asks for one tool's breakdown by its name, encoded", async () => {
    const urls = capture(200, { tool: "a__b", days: 90, by_user: [], by_surface_app: [], by_item: [] });

    await toolUsageApi.detail("a__b c", 90);

    expect(urls[0]).toBe("/api/admin/tool-usage/a__b%20c?days=90");
  });

  it("carries the status of a refusal", async () => {
    capture(403, { detail: "superusers only" });

    const err = await toolUsageApi.report(30).catch((e: unknown) => e);

    expect((err as HttpError).status).toBe(403);
  });
});
