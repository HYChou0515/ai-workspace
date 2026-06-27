import { describe, expect, it } from "vitest";

import type { KbCollection } from "../api/kb";
import {
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

describe("serializeCollectionsFile", () => {
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
