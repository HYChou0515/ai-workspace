import { describe, expect, it } from "vitest";

import { mockApi } from "./mock";
import { realApi } from "./real";

function stubFetch(body: unknown): typeof fetch {
  return (async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    })) as typeof fetch;
}

describe("getToolsCatalog", () => {
  it("real client returns the flat array from GET /tools", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = stubFetch([
      { name: "exec", label: "Exec", description: "Run a shell command." },
    ]);
    try {
      const rows = await realApi.getToolsCatalog();
      expect(rows[0]).toEqual({ name: "exec", label: "Exec", description: "Run a shell command." });
    } finally {
      globalThis.fetch = orig;
    }
  });

  it("mock client resolves a non-empty catalog", async () => {
    expect((await mockApi.getToolsCatalog()).length).toBeGreaterThan(0);
  });
});

describe("getItemTools", () => {
  it("real client returns both the pickable tools and the third-party list", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = stubFetch({
      tools: [
        { key: "exec", label: "Exec", description: "", default_on: true, pref: "off", effective: false },
      ],
      external: [
        { key: "wafer-history", version: "1.4.2", author: "W <w@x>", stale: false, unavailable: null },
      ],
    });
    try {
      const { tools, external } = await realApi.getItemTools("rca", "item1");
      expect(tools).toHaveLength(1);
      expect(tools[0].key).toBe("exec");
      expect(tools[0].pref).toBe("off");
      expect(tools[0].effective).toBe(false);
      expect(external[0].version).toBe("1.4.2");
      expect(external[0].author).toBe("W <w@x>");
    } finally {
      globalThis.fetch = orig;
    }
  });

  it("tolerates a backend that predates the third-party section", async () => {
    // A rolling deploy serves both versions at once, and a picker that threw
    // on the older one would be broken for the length of the rollout.
    const orig = globalThis.fetch;
    globalThis.fetch = stubFetch({ tools: [] });
    try {
      expect((await realApi.getItemTools("rca", "item1")).external).toEqual([]);
    } finally {
      globalThis.fetch = orig;
    }
  });
});
