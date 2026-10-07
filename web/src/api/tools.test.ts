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
      const got = await realApi.getItemTools("rca", "item1");
      const rows = got.tools;
      expect(rows).toHaveLength(2);
      expect(rows[0]!.key).toBe("exec");
      expect(rows[0]!.pref).toBe("off");
      expect(rows[1]!.version).toBe("1.4.2");
      expect(rows[1]!.author).toBe("W <w@x>");
      // A server that says nothing about the sandbox: nothing to update.
      expect(got.updateNeedsClose).toBe(false);
      expect(got.canClose).toBe(false);
    } finally {
      globalThis.fetch = orig;
    }
  });

  it("carries the running release and the close flags from the wire (plan-tool-running-version)", async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = stubFetch({
      tools: [
        {
          key: "wafer-history",
          label: "Wafer History",
          description: "",
          default_on: true,
          pref: "follow",
          effective: true,
          version: "1.4.2",
          running_version: "1.3.0",
        },
      ],
      update_needs_close: true,
      can_close: true,
    });
    try {
      const got = await realApi.getItemTools("rca", "item1");
      expect(got.tools[0]!.running_version).toBe("1.3.0");
      expect(got.updateNeedsClose).toBe(true);
      expect(got.canClose).toBe(true);
    } finally {
      globalThis.fetch = orig;
    }
  });
});
