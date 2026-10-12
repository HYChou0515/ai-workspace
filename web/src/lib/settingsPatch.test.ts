import { describe, expect, it } from "vitest";

import { diffPatches, flattenPatch, parsePatch, withoutPath } from "./settingsPatch";

describe("flattenPatch", () => {
  it("lists every changed value by its dotted path; a list or null is one value", () => {
    expect(
      flattenPatch({
        config: { agents: { presets: { a: { model: "m" }, gone: null } } },
        apps: { playground: { agent: { picker: [{ preset: "a" }] } } },
      }),
    ).toEqual([
      ["config.agents.presets.a.model", "m"],
      ["config.agents.presets.gone", null],
      ["apps.playground.agent.picker", [{ preset: "a" }]],
    ]);
  });
});

describe("diffPatches", () => {
  it("names what Apply adds, changes and takes back", () => {
    const before = { config: { llm: { base_url: "http://a" }, agents: { presets: { x: { model: "1" } } } } };
    const after = { config: { llm: { base_url: "http://b" }, kb: { retrieval_llm: { preset: "x" } } } };

    expect(diffPatches(before, after)).toEqual([
      { path: "config.llm.base_url", kind: "changed", before: "http://a", after: "http://b" },
      { path: "config.agents.presets.x.model", kind: "removed", before: "1" },
      { path: "config.kb.retrieval_llm.preset", kind: "added", after: "x" },
    ]);
  });

  it("is empty when nothing would change", () => {
    expect(diffPatches({ a: { b: [1, 2] } }, { a: { b: [1, 2] } })).toEqual([]);
  });
});

describe("withoutPath", () => {
  it("returns one value to its default and drops the parents it emptied", () => {
    const patch = { config: { agents: { presets: { a: { model: "m" } } }, llm: { base_url: "x" } } };

    expect(withoutPath(patch, "config.agents.presets.a.model")).toEqual({
      config: { llm: { base_url: "x" } },
    });
    expect(patch.config.agents.presets.a.model).toBe("m"); // a copy
  });
});

describe("parsePatch", () => {
  it("takes a JSON object", () => {
    expect(parsePatch('{"config": {}}')).toEqual({ ok: true, patch: { config: {} } });
  });

  it("refuses what is not JSON, or not an object, with the reason", () => {
    const broken = parsePatch("{");
    expect(broken.ok).toBe(false);
    expect(parsePatch("[1]")).toEqual({ ok: false, error: "not-an-object" });
  });
});
