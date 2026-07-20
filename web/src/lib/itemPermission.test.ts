import { describe, expect, it } from "vitest";

import { canChangeItemPermission, canWriteItem, parseItemPermission } from "./itemPermission";

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

import {
  canConverse,
  canReadChat,
  isDiscoverableOnly,
  itemGrantsFromPermission,
  itemPermissionFromGrants,
  itemRoleForVerbs,
} from "./itemPermission";

describe("item read-verb lock helpers (grill D1)", () => {
  it("read_chat / converse gate independently under restricted", () => {
    const p = { visibility: "restricted" as const, read_chat: ["user:me1"] };
    expect(canReadChat(p, ME, OWNER)).toBe(true);
    expect(canConverse(p, ME, OWNER)).toBe(false); // orthogonal — enter but can't talk
  });
  it("owner + public + private behave like authorize", () => {
    expect(canReadChat({ visibility: "private" }, OWNER, OWNER)).toBe(true);
    expect(canReadChat(undefined, ME, OWNER)).toBe(true); // absent ≡ public
    expect(canConverse({ visibility: "private" }, ME, OWNER)).toBe(false);
  });
  it("isDiscoverableOnly = read_meta but not read_chat (the 🔒 locked row)", () => {
    expect(isDiscoverableOnly({ visibility: "restricted", read_meta: ["user:me1"] }, ME, OWNER)).toBe(true);
    expect(
      isDiscoverableOnly(
        { visibility: "restricted", read_meta: ["user:me1"], read_chat: ["user:me1"] },
        ME,
        OWNER,
      ),
    ).toBe(false);
  });
});

describe("item role ↔ grant mapping (grill D2)", () => {
  it("picks the deepest nested role a verb set satisfies", () => {
    expect(itemRoleForVerbs(new Set(["read_meta"]))).toBe("discoverable");
    expect(itemRoleForVerbs(new Set(["read_meta", "read_chat"]))).toBe("in_workspace");
    expect(itemRoleForVerbs(new Set(["read_meta", "read_chat", "read_content", "converse"]))).toBe(
      "participant",
    );
    expect(itemRoleForVerbs(new Set())).toBeNull();
  });
  it("round-trips a Participant grant through the permission", () => {
    const perm = itemPermissionFromGrants(
      "restricted",
      [{ userId: "alice", role: "participant", verbs: new Set() }],
      { visibility: "private" },
    );
    expect(perm.read_chat).toContain("user:alice");
    expect(perm.converse).toContain("user:alice");
    expect(perm.edit_content ?? []).not.toContain("user:alice"); // not a Collaborator
    const back = itemGrantsFromPermission(perm, OWNER);
    expect(back).toEqual([{ userId: "alice", role: "participant", verbs: expect.any(Set) }]);
  });
  it("preserves group + all subjects and unmanaged verbs on save", () => {
    const perm = itemPermissionFromGrants(
      "restricted",
      [{ userId: "alice", role: "in_workspace", verbs: new Set() }],
      { visibility: "restricted", read_chat: ["group:team", "all"], change_permission: ["user:x"] },
    );
    expect(perm.read_chat).toEqual(expect.arrayContaining(["group:team", "all", "user:alice"]));
    expect(perm.change_permission).toEqual(["user:x"]); // unmanaged verb untouched
  });
  it("a Custom (non-nested) grant writes exactly its verbs", () => {
    const perm = itemPermissionFromGrants(
      "restricted",
      [{ userId: "bob", role: "reader", verbs: new Set(["read_meta", "read_content"]) }], // chat-less
      { visibility: "private" },
    );
    expect(perm.read_content).toContain("user:bob");
    expect(perm.read_chat ?? []).not.toContain("user:bob"); // custom omitted read_chat
  });
});

// Mirrors `perm/authorize.py` step 5, which special-cases `change_permission`:
// it is NEVER conferred by `public` visibility — only the owner, a superuser, or
// an explicit grant may rewire access control. A generic verb check would say
// "public ⇒ allowed" and hand the share control to every viewer.
describe("canChangeItemPermission", () => {
  it("allows the owner", () => {
    expect(canChangeItemPermission({ visibility: "private" }, "alice", "alice", false)).toBe(true);
  });
  it("allows a superuser who does not own the item", () => {
    expect(canChangeItemPermission({ visibility: "private" }, "root", "alice", true)).toBe(true);
  });
  it("allows an explicit change_permission grantee", () => {
    const perm = { visibility: "restricted" as const, change_permission: ["user:carol"] };
    expect(canChangeItemPermission(perm, "carol", "alice", false)).toBe(true);
  });
  it("denies a plain collaborator", () => {
    const perm = { visibility: "restricted" as const, converse: ["user:dave"] };
    expect(canChangeItemPermission(perm, "dave", "alice", false)).toBe(false);
  });
  it("denies a stranger on a PUBLIC item — public never confers change_permission", () => {
    expect(canChangeItemPermission({ visibility: "public" }, "eve", "alice", false)).toBe(false);
  });
  it("denies a stranger when the item has no permission object (legacy ≡ public)", () => {
    expect(canChangeItemPermission(undefined, "eve", "alice", false)).toBe(false);
  });
  it("honours an explicit `all` grant", () => {
    const perm = { visibility: "restricted" as const, change_permission: ["all"] };
    expect(canChangeItemPermission(perm, "eve", "alice", false)).toBe(true);
  });
});
