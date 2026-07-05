import { describe, expect, it } from "vitest";

import { canWriteItem, parseItemPermission } from "./itemPermission";

const OWNER = "owner1";
const ME = "me1";

describe("canWriteItem (mirrors backend perm/authorize for a write verb)", () => {
  it("the owner can always write (any visibility)", () => {
    expect(canWriteItem({ visibility: "private" }, OWNER, OWNER)).toBe(true);
  });
  it("absent permission ≡ public → writable", () => {
    expect(canWriteItem(undefined, ME, OWNER)).toBe(true);
  });
  it("public visibility → anyone writes", () => {
    expect(canWriteItem({ visibility: "public" }, ME, OWNER)).toBe(true);
  });
  it("private + non-owner → read-only even if a grant lists them", () => {
    expect(canWriteItem({ visibility: "private", edit_content: ["user:me1"] }, ME, OWNER)).toBe(false);
  });
  it("restricted + granted a write verb (user or all) → writable", () => {
    expect(canWriteItem({ visibility: "restricted", edit_content: ["user:me1"] }, ME, OWNER)).toBe(true);
    expect(canWriteItem({ visibility: "restricted", add_content: ["all"] }, ME, OWNER)).toBe(true);
    expect(canWriteItem({ visibility: "restricted", write_meta: ["user:me1"] }, ME, OWNER)).toBe(true);
  });
  it("restricted + not granted → read-only", () => {
    expect(canWriteItem({ visibility: "restricted", edit_content: ["user:someone"] }, ME, OWNER)).toBe(false);
  });
});

describe("parseItemPermission", () => {
  it("passes a well-formed permission object through", () => {
    expect(parseItemPermission({ visibility: "restricted", edit_content: ["user:a"] })).toMatchObject({
      visibility: "restricted",
    });
  });
  it("returns undefined for a non-object or one missing a valid visibility", () => {
    expect(parseItemPermission(undefined)).toBeUndefined();
    expect(parseItemPermission("nope")).toBeUndefined();
    expect(parseItemPermission({})).toBeUndefined();
    expect(parseItemPermission({ visibility: "bogus" })).toBeUndefined();
  });
});
