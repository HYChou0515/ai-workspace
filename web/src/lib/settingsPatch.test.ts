import { describe, expect, it } from "vitest";

import {
  diffEffective,
  diffPatches,
  flattenPatch,
  flattenParts,
  lineOfPath,
  parsePatch,
  withoutPath,
} from "./settingsPatch";

describe("lineOfPath", () => {
  const text = [
    "{", //                                            1
    '  "config": {', //                                2
    '    "agents": { "presets": { "a": {', //          3
    '      "model": "m",', //                           4
    '      "note": "the \\"config\\" word: {",', //     5
    '      "fallbacks": ["x9"]', //                     6
    "    } } }", //                                    7
    "  },", //                                         8
    '  "apps": { "playground": { "agent": {} } }', //  9
    "}",
  ].join("\n");

  it("finds the line a dotted path's key is written on", () => {
    expect(lineOfPath(text, "config.agents.presets.a.fallbacks")).toBe(6);
    expect(lineOfPath(text, "config.agents.presets.a.model")).toBe(4);
    expect(lineOfPath(text, "apps.playground")).toBe(9);
  });

  it("falls back to the deepest part of the path the text has", () => {
    // a problem can name a key the patch does not set (the effective value's)
    expect(lineOfPath(text, "apps.playground.agent.picker")).toBe(9);
    expect(lineOfPath(text, "config.agents.presets.a.llm.base_url")).toBe(3);
  });

  it("is not fooled by a key's name inside a string", () => {
    expect(lineOfPath('{"note": "\\"config\\": x"}', "config")).toBeNull();
  });

  it("answers null for a path the text does not have, or text that is not JSON", () => {
    expect(lineOfPath(text, "profiles.x")).toBeNull();
    expect(lineOfPath("{", "config")).toBeNull();
  });
});

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

    expect(withoutPath(patch, ["config", "agents", "presets", "a", "model"])).toEqual({
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


describe("a preset whose name holds a dot", () => {
  // review r1: paths joined and split on "." made `qwen2.5` read as `qwen2` -> `5`,
  // so its reset did nothing at all.
  const patch = { config: { agents: { presets: { "qwen2.5": { model: "m" } } } } };

  it("keeps the name whole in each value's parts", () => {
    expect(flattenParts(patch)).toEqual([
      { path: "config.agents.presets.qwen2.5.model", parts: ["config", "agents", "presets", "qwen2.5", "model"], value: "m" },
    ]);
  });

  it("resets that value", () => {
    expect(withoutPath(patch, ["config", "agents", "presets", "qwen2.5", "model"])).toEqual({});
  });
});


describe("diffEffective", () => {
  // review r2 (ux): comparing the two patches called a value that replaces a
  // deploy default "Added", with no old value; what changes is what RUNS.
  const defaults = { config: { agents: { presets: { a: { model: "default-a" } } } } };

  it("names a replaced default as changed, from the default", () => {
    const after = { config: { agents: { presets: { a: { model: "new-a" } } } } };

    expect(diffEffective(defaults, {}, after)).toEqual([
      { path: "config.agents.presets.a.model", kind: "changed", before: "default-a", after: "new-a" },
    ]);
  });

  it("names a value back to its default as changed, to the default", () => {
    const before = { config: { agents: { presets: { a: { model: "new-a" } } } } };

    expect(diffEffective(defaults, before, {})).toEqual([
      { path: "config.agents.presets.a.model", kind: "changed", before: "new-a", after: "default-a" },
    ]);
  });

  it("names a value the defaults lack as added, and a hidden preset as removed", () => {
    expect(diffEffective(defaults, {}, { config: { llm: { base_url: "x" } } })).toEqual([
      { path: "config.llm.base_url", kind: "added", after: "x" },
    ]);
    expect(diffEffective(defaults, {}, { config: { agents: { presets: { a: null } } } })).toEqual([
      { path: "config.agents.presets.a", kind: "removed", before: { model: "default-a" } },
    ]);
  });
});


describe("diffEffective under a hidden preset", () => {
  // review r3: a null ANCESTOR fell through to the defaults, so hiding a preset
  // read as "its model goes back to the default".
  const defaults = { foo: { model: "d" } };

  // confirmation round: a hide is ONE change — the preset stops running, all
  // of it — so it is said as the preset, not through the values under it.
  it("hiding a preset reads as the preset removed", () => {
    expect(diffEffective(defaults, { foo: { model: "x" } }, { foo: null })).toEqual([
      { path: "foo", kind: "removed", before: { model: "x" } },
    ]);
  });

  it("showing a hidden preset again reads as the preset added", () => {
    expect(diffEffective(defaults, { foo: null }, { foo: { model: "y" } })).toEqual([
      { path: "foo", kind: "added", after: { model: "y" } },
    ]);
  });

  it("is not fooled by a sibling whose name extends this one's with a dot", () => {
    // confirmation round: deeper-than was decided on JOINED paths, so a hide
    // of `qwen2` vanished beside `qwen2.5.model` — Apply said "nothing to apply".
    const both = { presets: { qwen2: { model: "a" }, "qwen2.5": { model: "b" } } };
    const published = { presets: { "qwen2.5": { model: "x" } } };
    const draft = { presets: { qwen2: null, "qwen2.5": { model: "x" } } };

    expect(diffEffective(both, published, draft)).toEqual([
      { path: "presets.qwen2", kind: "removed", before: { model: "a" } },
    ]);
  });

  it("does not take a sibling's values as under a hidden preset", () => {
    const both = { presets: { qwen2: { model: "a" }, "qwen2.5": { model: "b" } } };
    const draft = { presets: { qwen2: null, "qwen2.5": { model: "y" } } };

    expect(diffEffective(both, {}, draft)).toEqual([
      { path: "presets.qwen2", kind: "removed", before: { model: "a" } },
      { path: "presets.qwen2.5.model", kind: "changed", before: "b", after: "y" },
    ]);
  });
});
