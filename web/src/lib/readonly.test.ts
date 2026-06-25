import { describe, expect, it } from "vitest";

import { isReadOnlyPath } from "./readonly";

describe("isReadOnlyPath", () => {
  it("flags files under a .readonly/ directory at any depth", () => {
    expect(isReadOnlyPath("/.readonly/context-card.current.md")).toBe(true);
    expect(isReadOnlyPath(".readonly/x.md")).toBe(true);
    expect(isReadOnlyPath("/sub/.readonly/y")).toBe(true);
  });

  it("leaves normal files editable (a filename is not a directory segment)", () => {
    expect(isReadOnlyPath("/context-card.todo.md")).toBe(false);
    expect(isReadOnlyPath("/notes/readonly.md")).toBe(false);
    expect(isReadOnlyPath("/a.txt")).toBe(false);
  });
});
