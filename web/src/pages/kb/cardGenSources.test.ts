import { describe, expect, it } from "vitest";

import type { KbDocument } from "../../api/kb";
import { WIKI_ID_PREFIX, buildCardGenSources, wikiPageId } from "./cardGenSources";

const doc = (resource_id: string, path: string): KbDocument =>
  ({ resource_id, path }) as KbDocument;

describe("wikiPageId", () => {
  it("mirrors the backend _rid: {collection}{path} with every / → U+2215", () => {
    // WikiPage.resource_id = kb/wiki/store.py _rid — the leading-slash path is
    // appended to the (slash-free) collection id, then all "/" swap to U+2215.
    expect(wikiPageId("col1", "/entities/rz3.md")).toBe("col1∕entities∕rz3.md");
    expect(wikiPageId("col1", "/index.md")).toBe("col1∕index.md");
  });
});

describe("buildCardGenSources", () => {
  it("nests documents under Documents/ and maps each path to its resource id", () => {
    const { files, ids } = buildCardGenSources(
      "col1",
      [doc("id-a", "reflow.md"), doc("id-b", "sub/rz3.md")],
      [],
    );
    expect(files.map((f) => f.path)).toEqual(["Documents/reflow.md", "Documents/sub/rz3.md"]);
    expect(ids.get("Documents/reflow.md")).toBe("id-a");
    expect(ids.get("Documents/sub/rz3.md")).toBe("id-b");
  });

  it("nests wiki pages under Wiki/ and maps each to its TYPE-TAGGED wiki-page id", () => {
    const { files, ids } = buildCardGenSources("col1", [], ["/index.md", "/entities/rz3.md"]);
    expect(files.map((f) => f.path)).toEqual(["Wiki/index.md", "Wiki/entities/rz3.md"]);
    // wiki ids carry the `wiki:` tag so the backend routes them to the wiki reader.
    expect(ids.get("Wiki/index.md")).toBe(`${WIKI_ID_PREFIX}col1∕index.md`);
    expect(ids.get("Wiki/entities/rz3.md")).toBe(`${WIKI_ID_PREFIX}col1∕entities∕rz3.md`);
  });

  it("keeps a doc and a same-path wiki page distinct despite the id collision", () => {
    // A doc `index.md` and a wiki page `/index.md` encode to the SAME raw id
    // (`col1∕index.md`); the `wiki:` tag is what stops the wiki pick from
    // silently submitting the document's id.
    const { ids } = buildCardGenSources("col1", [doc("col1∕index.md", "index.md")], ["/index.md"]);
    expect(ids.get("Documents/index.md")).toBe("col1∕index.md");
    expect(ids.get("Wiki/index.md")).toBe(`${WIKI_ID_PREFIX}col1∕index.md`);
    expect(ids.get("Documents/index.md")).not.toBe(ids.get("Wiki/index.md"));
  });

  it("excludes .gitkeep placeholders from both sources", () => {
    const { files } = buildCardGenSources(
      "col1",
      [doc("id-a", "reflow.md"), doc("id-k", ".gitkeep")],
      ["/index.md", "/.gitkeep"],
    );
    expect(files.map((f) => f.path)).toEqual(["Documents/reflow.md", "Wiki/index.md"]);
  });
});
