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
  it("real client unwraps the { tools: [...] } envelope, provenance and all", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = stubFetch({
      tools: [
        { key: "exec", label: "Exec", description: "", default_on: true, pref: "off", effective: false },
        {
          key: "wafer-history",
          label: "Wafer History",
          description: "",
          default_on: true,
          pref: "follow",
          effective: true,
          external: true,
          version: "1.4.2",
          author: "W <w@x>",
        },
      ],
    });
    try {
      const rows = await realApi.getItemTools("rca", "item1");
      expect(rows).toHaveLength(2);
      expect(rows[0].key).toBe("exec");
      expect(rows[0].pref).toBe("off");
      expect(rows[1].version).toBe("1.4.2");
      expect(rows[1].author).toBe("W <w@x>");
    } finally {
      globalThis.fetch = orig;
    }
  });
});
