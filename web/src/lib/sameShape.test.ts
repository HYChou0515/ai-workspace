import { describe, expect, it } from "vitest";

import { sameShape } from "./sameShape";

describe("sameShape (dirty comparison for modal exits, #779)", () => {
  it("ignores key order, which a sparse override reshuffles on every edit", () => {
    // SkillsModal's setState deletes a key for "follow" and re-adds it for
    // on/off, moving it to the end. Set a skill to follow and back to on and
    // JSON.stringify differs while nothing changed — the modal would then claim
    // unsaved work on every exit, which is how people learn to click through
    // the prompt.
    expect(sameShape({ a: true, b: false }, { b: false, a: true })).toBe(true);
    expect(sameShape({ a: true }, { a: false })).toBe(false);
    expect(sameShape({ a: true }, { a: true, b: true })).toBe(false);
  });

  it("ignores array order, so removing and re-adding a grant is not an edit", () => {
    expect(sameShape([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "a" }])).toBe(true);
    expect(sameShape([{ id: "a" }], [{ id: "a" }, { id: "b" }])).toBe(false);
  });

  it("sees inside a Set — JSON.stringify renders every Set as {}", () => {
    // ItemGrant.verbs is a Set. Compared through JSON.stringify, granting a
    // custom verb looks identical to granting none, so the dialog would close
    // without asking and drop the change silently — the exact failure the guard
    // exists to prevent.
    expect(sameShape({ verbs: new Set(["read"]) }, { verbs: new Set() })).toBe(false);
    expect(sameShape({ verbs: new Set(["read", "write"]) }, { verbs: new Set(["write", "read"]) })).toBe(
      true,
    );
  });

  it("distinguishes an empty Set from an empty object", () => {
    expect(sameShape(new Set(), {})).toBe(false);
  });

  it("compares primitives and nulls the obvious way", () => {
    expect(sameShape("x", "x")).toBe(true);
    expect(sameShape(null, undefined)).toBe(false);
    expect(sameShape(1, "1")).toBe(false);
  });
});
