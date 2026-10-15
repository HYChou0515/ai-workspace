import { describe, expect, it } from "vitest";

import {
  diffEffective,
  dumpPatch,
  effectiveOf,
  patchOf,
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

  it("says a hidden or shown preset as what runs, the defaults under it included", () => {
    // final review 6: the patch's own fragment was said, not the merged preset
    // — "added {model: y}" for a preset that runs with its default base_url.
    const full = { foo: { model: "d", llm: { base_url: "u" } } };

    expect(diffEffective(full, { foo: null }, { foo: { model: "y" } })).toEqual([
      { path: "foo", kind: "added", after: { model: "y", llm: { base_url: "u" } } },
    ]);
    expect(diffEffective(full, { foo: { model: "x" } }, { foo: null })).toEqual([
      { path: "foo", kind: "removed", before: { model: "x", llm: { base_url: "u" } } },
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

describe("the draft as YAML (M9, as config.yaml is written)", () => {
  const yaml = [
    "config:", //               1
    "  agents:", //             2
    "    presets:", //          3
    "      qwen2.5:", //        4
    "        model: x", //      5
    "        fallbacks:", //    6
    "          - b", //         7
    "apps:", //                 8
    "  playground: {}", //      9
  ].join("\n");

  it("reads YAML into the patch, and JSON still, which is YAML", () => {
    expect(parsePatch(yaml)).toEqual({
      ok: true,
      patch: {
        config: { agents: { presets: { "qwen2.5": { model: "x", fallbacks: ["b"] } } } },
        apps: { playground: {} },
      },
    });
    expect(parsePatch('{"config": {}}')).toEqual({ ok: true, patch: { config: {} } });
    expect(parsePatch("")).toEqual({ ok: true, patch: {} });
  });

  it("keeps a date-like text a text (no YAML surprises)", () => {
    expect(parsePatch("note: 2026-10-13")).toEqual({ ok: true, patch: { note: "2026-10-13" } });
  });

  it("names the line a YAML error is on", () => {
    const broken = parsePatch("config:\n  a: 1\n b: 2");
    expect(broken.ok).toBe(false);
    expect(broken.ok ? null : broken.line).toBe(3);
  });

  it("finds the line a path is written on", () => {
    expect(lineOfPath(yaml, "config.agents.presets.qwen2.5.model")).toBe(5);
    expect(lineOfPath(yaml, "config.agents.presets.qwen2.5.fallbacks")).toBe(6);
    expect(lineOfPath(yaml, "apps.playground.agent")).toBe(9);
    expect(lineOfPath(yaml, "profiles.x")).toBeNull();
  });

  it("does not index keys inside a list item — a patch replaces a list whole", () => {
    const list = ["config:", "  agents:", "    kb_chat:", "      - preset: a", "        name: X"].join("\n");
    // a problem about an entry is marked on the list, not on a sibling's key
    expect(lineOfPath(list, "config.agents.kb_chat.preset")).toBe(3);
    expect(lineOfPath(list, "config.agents.kb_chat.name")).toBe(3);
  });

  it("writes a patch back as YAML that reads back the same", () => {
    const patch = { config: { agents: { presets: { "qwen2.5": { model: "x" } } } } };
    const text = dumpPatch(patch);
    expect(text).toContain("qwen2.5:");
    expect(text).not.toContain("{");
    expect(parsePatch(text)).toEqual({ ok: true, patch });
    expect(dumpPatch({})).toBe("{}\n");
  });
});

describe("editing the whole settings (M23): what is shown, and what is stored", () => {
  const base = {
    config: {
      server: { port: 9090 },
      llm: { base_url: "http://a", api_key: "*** set ***" },
      agents: { presets: { a: { model: "m1", env: { T: "*** set ***" } }, b: { model: "m2" } } },
    },
    apps: { playground: { title: "P" } },
  };

  it("shows the defaults with the saved draft over them", () => {
    expect(effectiveOf(base, { config: { agents: { presets: { a: { model: "m9" }, b: null } } } })).toEqual({
      ...base,
      config: { ...base.config, agents: { presets: { a: { model: "m9", env: { T: "*** set ***" } }, b: null } } },
    });
  });

  it("stores only what differs from the defaults", () => {
    const edited = structuredClone(base);
    edited.config.agents.presets.a.model = "m9";

    expect(patchOf(base, edited)).toEqual({ config: { agents: { presets: { a: { model: "m9" } } } } });
  });

  it("takes a pasted fragment as just that change — what is left out is the default", () => {
    const fragment = { config: { agents: { presets: { a: { model: "m9" } } } } };

    expect(patchOf(base, fragment)).toEqual(fragment);
  });

  it("drops an unchanged section the page does not edit, and keeps a changed one for the check", () => {
    const pasted = structuredClone(base);
    expect(patchOf(base, pasted)).toEqual({});
    pasted.config.server.port = 1;
    expect(patchOf(base, pasted)).toEqual({ config: { server: { port: 1 } } });
  });

  it("takes a secret's mask — the page's or the boot dump's — as no change", () => {
    const pasted = structuredClone(base);
    pasted.config.llm.api_key = "*** set (17 chars) via ${OPENAI_KEY} ***";
    pasted.config.agents.presets.a.env.T = "*** set (3 chars) ***";

    expect(patchOf(base, pasted)).toEqual({});
  });

  it("shows a hidden preset as null, so the text keeps the hide", () => {
    const hide = { config: { agents: { presets: { b: null } } } };
    const shown = effectiveOf(base, hide);

    expect((shown.config as { agents: { presets: Record<string, unknown> } }).agents.presets.b).toBeNull();
    expect(patchOf(base, shown)).toEqual(hide);
  });

  it("shows any null the draft holds where it was written, so the text keeps it", () => {
    // review 17: a null outside a preset (refused, but a draft with problems can
    // be saved) vanished from the text — read back it was {}, never the saved
    // draft, and the next Save dropped it in silence.
    const draft = { config: { llm: { base_url: null } } };
    const shown = effectiveOf(base, draft);

    expect((shown.config as { llm: Record<string, unknown> }).llm.base_url).toBeNull();
    expect(patchOf(base, shown)).toEqual(draft);
  });

  it("takes a list pasted back with its secrets masked another way as no change", () => {
    // review 17: the page masks a usage entry's key `*** set ***`, the boot dump
    // `*** set (N chars) via ${X} ***`; a list was compared whole, so the dump
    // pasted back unchanged stored the list — and the check refused it.
    const withList = { config: { agents: { kb_chat: [{ name: "k", llm: { api_key: "*** set ***" } }] } } };
    const pasted = { config: { agents: { kb_chat: [{ name: "k", llm: { api_key: "*** set (6 chars) via ${X} ***" } }] } } };

    expect(patchOf(withList, pasted)).toEqual({});
    pasted.config.agents.kb_chat[0].name = "renamed";
    expect(patchOf(withList, pasted)).toEqual(pasted);
  });

  it("hides a preset only where null is written for it", () => {
    expect(patchOf(base, { config: { agents: { presets: { b: null } } } })).toEqual({
      config: { agents: { presets: { b: null } } },
    });
  });

  it("takes a list written with its keys in another order as no change", () => {
    // CLAUDE.md: compare with sameShape, never JSON.stringify — a pasted usage
    // entry reorders its keys and is still the same entry.
    const listed = { config: { agents: { kb_chat: [{ preset: "a", name: "A" }] } } };

    expect(patchOf(listed, { config: { agents: { kb_chat: [{ name: "A", preset: "a" }] } } })).toEqual({});
  });

  it("keeps a new preset whole", () => {
    expect(patchOf(base, { config: { agents: { presets: { c: { model: "m3" } } } } })).toEqual({
      config: { agents: { presets: { c: { model: "m3" } } } },
    });
  });
});
