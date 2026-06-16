// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

function fetchSpy(body: string) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(body, {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

describe("countAppItems", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /{route}/count with the filter and returns the server count", async () => {
    const calls = fetchSpy("7");
    const n = await realApi.countAppItems("/rca-investigation", {
      data_conditions: '[{"field_path":"status","operator":"eq","value":"done"}]',
    });
    expect(n).toBe(7);
    expect(calls[0]).toContain("/rca-investigation/count?");
    expect(calls[0]).toContain("data_conditions=");
  });
});
