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

  it("treats arrays as sets — the documented limit", () => {
    // Every caller today holds a set-like array (grants, group grants, tag
    // fields whose input only appends and de-duplicates). This test exists so
    // the trade-off is visible rather than discovered: a deliberately ordered
    // list must NOT be compared with this, because a reordering would read as
    // no change and the modal would close without asking.
    expect(sameShape(["a", "b"], ["b", "a"])).toBe(true);
  });

  it("sees a Date change — Object.entries(new Date()) is [], the Set bug again", () => {
    // This comparator was written because JSON.stringify renders every Set as
    // {}. A plain object walk reproduces that for every class instance with no
    // own enumerable properties: Date, URL, RegExp. No caller holds one today,
    // but this is now the project's dirty comparator, and the first modal to
    // snapshot a date field would read every change as unchanged and close
    // without asking — silent loss, the direction that matters.
    expect(sameShape(new Date(1), new Date(2))).toBe(false);
    expect(sameShape(new Date(1), new Date(1))).toBe(true);
    expect(sameShape(new Date(0), {})).toBe(false);
    expect(sameShape(/a/, /b/)).toBe(false);
  });

  it("refuses an unknown class instance instead of comparing it equal to everything", () => {
    class Money {
      constructor(readonly cents: number) {}
    }
    // Loud beats silent: a call site that hands this an unhandled type finds out
    // at once, rather than getting a modal that has quietly stopped noticing
    // edits to that field.
    expect(() => sameShape(new Money(1), new Money(2))).toThrow(/no canonical form/);
  });
});
