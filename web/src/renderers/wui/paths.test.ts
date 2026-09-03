import { describe, expect, it } from "vitest";

import {
  resolveAssetPath,
  resolveReadPath,
  resolveInFolder,
  resolveWritePath,
  wuiFolder,
} from "./paths";

describe("wuiFolder", () => {
  it("is the folder holding the view file", () => {
    expect(wuiFolder("/sales/page.ai.yaml")).toBe("/sales");
    expect(wuiFolder("/a/b/c/page.ai.yaml")).toBe("/a/b/c");
  });

  it("is the root for a view file at the top level", () => {
    // A WUI at the workspace root is degenerate but not an error: its folder is
    // the whole workspace, so the scope check below still has something to say.
    expect(wuiFolder("/page.ai.yaml")).toBe("");
  });
});

describe("resolveInFolder", () => {
  it("joins a folder-relative path onto the folder", () => {
    expect(resolveInFolder("/sales", "app.js")).toBe("/sales/app.js");
    expect(resolveInFolder("/sales", "./app.js")).toBe("/sales/app.js");
    expect(resolveInFolder("/sales", "assets/logo.png")).toBe("/sales/assets/logo.png");
  });

  it("resolves an interior `..` without leaving the folder", () => {
    expect(resolveInFolder("/sales", "assets/../app.js")).toBe("/sales/app.js");
    expect(resolveInFolder("/sales", "a/b/../../app.js")).toBe("/sales/app.js");
  });

  it("refuses a path that climbs out of the folder", () => {
    // The whole write scope rests on this: `..` is the way out of a folder, and
    // a page that reaches `/notes.md` through one has escaped its own sandbox.
    expect(resolveInFolder("/sales", "../notes.md")).toBeNull();
    expect(resolveInFolder("/sales", "a/../../notes.md")).toBeNull();
    expect(resolveInFolder("/sales", "..")).toBeNull();
    expect(resolveInFolder("/sales", "../")).toBeNull();
  });

  it("refuses an absolute path, which is not folder-relative at all", () => {
    expect(resolveInFolder("/sales", "/notes.md")).toBeNull();
  });

  it("refuses a sibling folder whose name merely starts the same", () => {
    // `/sales2` is not inside `/sales`, though a naive prefix test says it is.
    expect(resolveInFolder("/sales", "../sales2/x.js")).toBeNull();
  });

  it("collapses redundant separators rather than producing an unreachable path", () => {
    expect(resolveInFolder("/sales", "a//b.js")).toBe("/sales/a/b.js");
    expect(resolveInFolder("/sales", "./a/./b.js")).toBe("/sales/a/b.js");
  });

  it("refuses an empty reference", () => {
    expect(resolveInFolder("/sales", "")).toBeNull();
    expect(resolveInFolder("/sales", ".")).toBeNull();
  });

  it("scopes to the whole workspace for a root-level WUI", () => {
    expect(resolveInFolder("", "app.js")).toBe("/app.js");
    expect(resolveInFolder("", "../escape.js")).toBeNull();
  });
});

describe("resolveReadPath", () => {
  it("reads anywhere in the item when given an absolute path", () => {
    // Reading broadly is the point: secondary analysis of the item's real data
    // is the use case, and the user opening the page can already see all of it.
    expect(resolveReadPath("/sales", "/notes.md")).toBe("/notes.md");
    expect(resolveReadPath("/sales", "/issues/5.md")).toBe("/issues/5.md");
  });

  it("treats a bare path as next to the page, which is what an author means", () => {
    expect(resolveReadPath("/sales", "data.json")).toBe("/sales/data.json");
  });

  it("normalises an absolute path rather than trusting it", () => {
    expect(resolveReadPath("/sales", "/a/../b.md")).toBe("/b.md");
    expect(resolveReadPath("/sales", "/a//b.md")).toBe("/a/b.md");
  });

  it("refuses to climb above the workspace root", () => {
    expect(resolveReadPath("/sales", "/../secret")).toBeNull();
    expect(resolveReadPath("/sales", "/")).toBeNull();
  });
});

describe("resolveWritePath", () => {
  it("writes inside the page's own folder", () => {
    expect(resolveWritePath("/sales", "data.json")).toBe("/sales/data.json");
    expect(resolveWritePath("/sales", "/sales/data.json")).toBe("/sales/data.json");
  });

  it("refuses anywhere else in the item, however it is spelled", () => {
    // Reading is broad and writing is narrow: a page must not be able to
    // overwrite the item's notes or the folder next door.
    expect(resolveWritePath("/sales", "/notes.md")).toBeNull();
    expect(resolveWritePath("/sales", "../notes.md")).toBeNull();
    expect(resolveWritePath("/sales", "/sales/../notes.md")).toBeNull();
  });

  it("refuses a sibling folder that merely shares the prefix", () => {
    expect(resolveWritePath("/sales", "/sales2/x.json")).toBeNull();
    expect(resolveWritePath("/sales", "/sales.bak/x.json")).toBeNull();
  });

  it("refuses the folder itself, which is not a file", () => {
    expect(resolveWritePath("/sales", "/sales")).toBeNull();
  });

  it("refuses every write from a view file sitting at the workspace root", () => {
    // This used to return the path unchanged, on the reasoning that such a
    // page's folder IS the root — which quietly removed the containment the
    // rest of this module exists for. A page there could overwrite the item's
    // notes, another WUI's `index.html`, and the skills and workflows the
    // platform later runs on the user's behalf. "No own folder" has to mean
    // nothing to write to, not everything.
    expect(resolveWritePath("", "data.json")).toBeNull();
    expect(resolveWritePath("", "/deep/data.json")).toBeNull();
  });

  it("still lets a root-level WUI READ, which was never the risk", () => {
    expect(resolveReadPath("", "/notes.md")).toBe("/notes.md");
    expect(resolveReadPath("", "data.json")).toBe("/data.json");
  });
});

describe("resolveAssetPath", () => {
  // What a built entry needs: `dist/index.html` referencing `./assets/x.js`
  // means the file next to IT, not next to the view file. Resolving from the
  // WUI folder instead pointed every reference one directory too high, and the
  // page rendered with nothing inlined — the same lost-origin mistake that
  // broke markdown images (#717).
  it("resolves a reference against the ENTRY's directory", () => {
    expect(resolveAssetPath("/app", "/app/dist", "assets/x.js")).toBe("/app/dist/assets/x.js");
    expect(resolveAssetPath("/app", "/app/dist", "./assets/x.js")).toBe("/app/dist/assets/x.js");
  });

  it("is the folder itself when the entry sits at the top", () => {
    expect(resolveAssetPath("/app", "/app", "app.js")).toBe("/app/app.js");
  });

  it("lets a nested entry reach a sibling of the view file", () => {
    // The boundary is the WUI FOLDER, not the entry's directory: a built page
    // referencing `../logo.png` is still inside its own WUI.
    expect(resolveAssetPath("/app", "/app/dist", "../logo.png")).toBe("/app/logo.png");
  });

  it("still refuses anything outside the WUI folder", () => {
    expect(resolveAssetPath("/app", "/app/dist", "../../notes.md")).toBeNull();
    expect(resolveAssetPath("/app", "/app", "../notes.md")).toBeNull();
    expect(resolveAssetPath("/app", "/app/dist", "/notes.md")).toBeNull();
  });

  it("refuses a sibling folder that merely shares the prefix", () => {
    expect(resolveAssetPath("/app", "/app/dist", "../../app2/x.js")).toBeNull();
  });
});
