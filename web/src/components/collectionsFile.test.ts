import { describe, expect, it } from "vitest";

import type { KbCollection } from "../api/kb";
import {
  entriesFromGroups,
  groupEntriesByTier,
  parseCollectionsFile,
  serializeCollectionsFile,
  splitSelection,
} from "./collectionsFile";

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  is_global: false,
  ...over,
});

describe("parseCollectionsFile", () => {
  it("treats a missing file (null) as an empty selection, no warning", () => {
    expect(parseCollectionsFile(null)).toEqual({ status: "missing", entries: [], selectedIds: [], ignored: 0 });
  });

  it("treats a blank / whitespace-only file as missing (not corrupt)", () => {
    expect(parseCollectionsFile("")).toMatchObject({ status: "missing", entries: [] });
    expect(parseCollectionsFile("   \n ")).toMatchObject({ status: "missing", entries: [] });
  });

  it("flags an unparseable file as invalid (warn before overwrite)", () => {
    expect(parseCollectionsFile("{not json")).toEqual({ status: "invalid", entries: [], selectedIds: [], ignored: 0 });
  });

  it("flags valid JSON that is not an array as invalid", () => {
    expect(parseCollectionsFile('{"id":"c1"}')).toMatchObject({ status: "invalid", entries: [] });
    expect(parseCollectionsFile("42")).toMatchObject({ status: "invalid", entries: [] });
  });

  it("keeps well-formed {id,name} entries in file order", () => {
    const p = parseCollectionsFile('[{"id":"a","name":"Alpha"},{"id":"b","name":"Beta"}]');
    expect(p.status).toBe("ok");
    expect(p.entries).toEqual([
      { id: "a", name: "Alpha" },
      { id: "b", name: "Beta" },
    ]);
    expect(p.selectedIds).toEqual(["a", "b"]);
    expect(p.ignored).toBe(0);
  });

  it("tolerates malformed entries the way the backend does — drops them and counts them", () => {
    const p = parseCollectionsFile('[{"id":"a"},{"name":"no id"},5,{"id":""},null]');
    expect(p.status).toBe("ok");
    expect(p.entries).toEqual([{ id: "a", name: "" }]);
    expect(p.ignored).toBe(4);
  });

  it("de-dupes repeated ids without counting them as ignored", () => {
    const p = parseCollectionsFile('[{"id":"a","name":"A"},{"id":"a","name":"A again"}]');
    expect(p.entries).toEqual([{ id: "a", name: "A" }]);
    expect(p.ignored).toBe(0);
  });
});

describe("parseCollectionsFile — priority tiers (#280)", () => {
  it("keeps a valid integer tier on the entry", () => {
    const p = parseCollectionsFile('[{"id":"a","name":"A","tier":0},{"id":"b","name":"B","tier":10}]');
    expect(p.entries).toEqual([
      { id: "a", name: "A", tier: 0 },
      { id: "b", name: "B", tier: 10 },
    ]);
  });

  it("ignores a non-integer tier (entry kept, no tier — tolerant like the backend)", () => {
    const p = parseCollectionsFile('[{"id":"a","name":"A","tier":"oops"}]');
    expect(p.entries).toEqual([{ id: "a", name: "A" }]);
  });
});

describe("groupEntriesByTier (#280)", () => {
  it("groups entries by tier, ranked ascending; absent tier counts as 0", () => {
    const groups = groupEntriesByTier([
      { id: "a", name: "A", tier: 0 },
      { id: "b", name: "B" }, // absent ⇒ tier 0
      { id: "d", name: "D", tier: 20 },
      { id: "c", name: "C", tier: 10 },
    ]);
    expect(groups).toEqual([
      [
        { id: "a", name: "A", tier: 0 },
        { id: "b", name: "B" },
      ],
      [{ id: "c", name: "C", tier: 10 }],
      [{ id: "d", name: "D", tier: 20 }],
    ]);
  });

  it("returns an empty list for no entries", () => {
    expect(groupEntriesByTier([])).toEqual([]);
  });
});

describe("entriesFromGroups (#280)", () => {
  it("flattens ordered groups into entries with sparse tier ints (0, 10, 20)", () => {
    const entries = entriesFromGroups([
      [
        { id: "a", name: "A" },
        { id: "b", name: "B" },
      ],
      [{ id: "c", name: "C" }],
    ]);
    expect(entries).toEqual([
      { id: "a", name: "A", tier: 0 },
      { id: "b", name: "B", tier: 0 },
      { id: "c", name: "C", tier: 10 },
    ]);
  });

  it("drops empty groups so an emptied tier doesn't shift ranks", () => {
    const entries = entriesFromGroups([[{ id: "a", name: "A" }], [], [{ id: "b", name: "B" }]]);
    expect(entries).toEqual([
      { id: "a", name: "A", tier: 0 },
      { id: "b", name: "B", tier: 10 },
    ]);
  });
});

describe("serializeCollectionsFile", () => {
  it("emits a non-zero tier but omits tier 0 (keeps a flat file flat, git-friendly)", () => {
    const out = serializeCollectionsFile([
      { id: "a", name: "A", tier: 0 },
      { id: "c", name: "C", tier: 10 },
    ]);
    expect(JSON.parse(out)).toEqual([
      { id: "a", name: "A" },
      { id: "c", name: "C", tier: 10 },
    ]);
  });

  it("emits 2-space pretty JSON with only id + name, in order", () => {
    const out = serializeCollectionsFile([
      { id: "a", name: "Alpha" },
      { id: "b", name: "Beta" },
    ]);
    expect(out).toBe(
      '[\n  {\n    "id": "a",\n    "name": "Alpha"\n  },\n  {\n    "id": "b",\n    "name": "Beta"\n  }\n]',
    );
  });

  it("emits an empty array for no selection", () => {
    expect(serializeCollectionsFile([])).toBe("[]");
  });
});

describe("splitSelection", () => {
  it("partitions selected ids into ones present in the live list vs orphans", () => {
    const available = [coll({ resource_id: "a" }), coll({ resource_id: "b" })];
    expect(splitSelection(["a", "gone", "b"], available)).toEqual({
      known: ["a", "b"],
      orphans: ["gone"],
    });
  });
});
