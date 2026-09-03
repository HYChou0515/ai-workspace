import { describe, expect, it } from "vitest";

import { resolveInFolder, wuiFolder } from "./paths";

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
