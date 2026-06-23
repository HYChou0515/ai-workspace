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

/** A specstar list/get entry: domain fields under `data`, the always-present
 * created/updated who+when under `revision_info`. */
function entry() {
  return {
    data: { title: "Reflow drift", owner: "alice" },
    revision_info: {
      uid: "u1",
      resource_id: "INC-1",
      revision_id: "rev-1",
      created_time: "2026-06-15T08:00:00Z",
      updated_time: "2026-06-20T12:00:00Z",
      created_by: "alice",
      updated_by: "bob",
    },
  };
}

describe("listAppItems", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces created_time and created_by from revision_info onto each item", async () => {
    fetchSpy(JSON.stringify([entry()]));
    const [item] = await realApi.listAppItems("/rca-investigation");
    expect(item.created_time).toBe("2026-06-15T08:00:00Z");
    expect(item.created_by).toBe("alice");
  });
});

describe("getAppItem", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces created_time and created_by from revision_info", async () => {
    fetchSpy(JSON.stringify(entry()));
    const item = await realApi.getAppItem("/rca-investigation", "INC-1");
    expect(item.created_time).toBe("2026-06-15T08:00:00Z");
    expect(item.created_by).toBe("alice");
  });
});
