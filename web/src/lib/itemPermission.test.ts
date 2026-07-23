import { describe, expect, it } from "vitest";

import { canChangeItemPermission, canWriteItem, itemVisibility, parseItemPermission } from "./itemPermission";

const OWNER = "owner1";
const ME = "me1";

describe("canWriteItem (mirrors backend perm/authorize for a write verb)", () => {
  it("the owner can always write (any visibility)", () => {
    expect(canWriteItem({ visibility: "private" }, OWNER, OWNER, false)).toBe(true);
  });
  it("absent permission ≡ public → writable", () => {
    expect(canWriteItem(undefined, ME, OWNER, false)).toBe(true);
  });
  it("public visibility → anyone writes", () => {
    expect(canWriteItem({ visibility: "public" }, ME, OWNER, false)).toBe(true);
  });
  it("private + non-owner → read-only even if a grant lists them", () => {
    expect(canWriteItem({ visibility: "private", edit_content: ["user:me1"] }, ME, OWNER, false)).toBe(false);
  });
  it("restricted + granted a write verb (user or all) → writable", () => {
    expect(canWriteItem({ visibility: "restricted", edit_content: ["user:me1"] }, ME, OWNER, false)).toBe(true);
    expect(canWriteItem({ visibility: "restricted", add_content: ["all"] }, ME, OWNER, false)).toBe(true);
    expect(canWriteItem({ visibility: "restricted", write_meta: ["user:me1"] }, ME, OWNER, false)).toBe(true);
  });
  it("restricted + not granted → read-only", () => {
    expect(canWriteItem({ visibility: "restricted", edit_content: ["user:someone"] }, ME, OWNER, false)).toBe(false);
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
  canReadItemContent,
  hasItemVerb,
  isDiscoverableOnly,
  itemGrantsFromPermission,
  itemGroupGrantsFromPermission,
  itemPermissionFromGrants,
  itemRoleForVerbs,
} from "./itemPermission";

describe("item read-verb lock helpers (grill D1)", () => {
  it("read_chat / converse gate independently under restricted", () => {
    const p = { visibility: "restricted" as const, read_chat: ["user:me1"] };
    expect(canReadChat(p, ME, OWNER, false)).toBe(true);
    expect(canConverse(p, ME, OWNER, false)).toBe(false); // orthogonal — enter but can't talk
  });
  it("owner + public + private behave like authorize", () => {
    expect(canReadChat({ visibility: "private" }, OWNER, OWNER, false)).toBe(true);
    expect(canReadChat(undefined, ME, OWNER, false)).toBe(true); // absent ≡ public
    expect(canConverse({ visibility: "private" }, ME, OWNER, false)).toBe(false);
  });
  it("isDiscoverableOnly = read_meta but not read_chat (the 🔒 locked row)", () => {
    expect(isDiscoverableOnly({ visibility: "restricted", read_meta: ["user:me1"] }, ME, OWNER, false)).toBe(true);
    expect(
      isDiscoverableOnly(
        { visibility: "restricted", read_meta: ["user:me1"], read_chat: ["user:me1"] },
        ME,
        OWNER,
        false,
      ),
    ).toBe(false);
  });
});

// The backend (`perm/authorize.py` step 2) lets a direct human superuser bypass
// EVERY verb, and the item list scope honours that — so an admin sees other
// people's private items. The FE read gates did not, so the admin could see the
// row, enter the item, and then find the whole workspace missing: `hasItemVerb`
// hit `visibility === "private"` and returned false. #543 fixed exactly one
// helper (`canChangeItemPermission`); these four were left behind.
describe("superuser bypasses every item read gate (mirrors authorize step 2)", () => {
  const ROOT = "root";
  const privatePerm = { visibility: "private" as const };

  it("hasItemVerb grants any verb on a private item the superuser does not own", () => {
    for (const verb of ["read_meta", "read_chat", "read_content", "converse", "execute"] as const) {
      expect(hasItemVerb(privatePerm, ROOT, OWNER, verb, true)).toBe(true);
      expect(hasItemVerb(privatePerm, ROOT, OWNER, verb, false)).toBe(false);
    }
  });

  it("the read wrappers follow — this is the workspace that went blank", () => {
    expect(canReadItemContent(privatePerm, ROOT, OWNER, true)).toBe(true);
    expect(canReadChat(privatePerm, ROOT, OWNER, true)).toBe(true);
    expect(canConverse(privatePerm, ROOT, OWNER, true)).toBe(true);
  });

  it("a superuser is never the 🔒 discoverable-only row (they can enter)", () => {
    const discoverable = { visibility: "restricted" as const, read_meta: ["user:root"] };
    expect(isDiscoverableOnly(discoverable, ROOT, OWNER, true)).toBe(false);
    expect(isDiscoverableOnly(discoverable, ROOT, OWNER, false)).toBe(true);
  });

  it("canWriteItem too — the server accepts a superuser write, so don't hide it", () => {
    expect(canWriteItem(privatePerm, ROOT, OWNER, true)).toBe(true);
    expect(canWriteItem(privatePerm, ROOT, OWNER, false)).toBe(false);
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
  it("round-trips group grants, keeps `all`, and leaves unmanaged verbs untouched (#608)", () => {
    const perm = itemPermissionFromGrants(
      "restricted",
      [{ userId: "alice", role: "in_workspace", verbs: new Set() }],
      { visibility: "restricted", read_chat: ["group:team", "all"], change_permission: ["user:x"] },
      [{ groupId: "team", role: "in_workspace" }], // group grants now managed explicitly
    );
    // the group round-trips (in_workspace grants read_chat), `all` survives, alice added
    expect(perm.read_chat).toEqual(expect.arrayContaining(["group:team", "all", "user:alice"]));
    expect(perm.change_permission).toEqual(["user:x"]); // unmanaged verb untouched
  });

  it("DROPS a group grant that isn't passed back (dialog must round-trip them, #608)", () => {
    const perm = itemPermissionFromGrants(
      "restricted",
      [],
      { visibility: "restricted", read_chat: ["group:team", "all"] },
    );
    expect(perm.read_chat).toEqual(["all"]); // group dropped, `all` kept
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

describe("#578 itemVisibility — display fold", () => {
  it("treats an absent permission as public, matching the backend", () => {
    // WorkItemBase.permission: absent ≡ public (legacy rows, never migrated).
    expect(itemVisibility(undefined)).toBe("public");
    expect(itemVisibility(null)).toBe("public");
  });

  it("reports an unreadable permission as unknown, NOT public", () => {
    // Folding a permission we failed to parse into "public" would have the table
    // assert "everyone can open this" about an item whose setting it could not
    // read — silently relabelling it world-readable on any FE/BE version skew.
    expect(itemVisibility({ visibility: "some-future-value" })).toBe("unknown");
    expect(itemVisibility({})).toBe("unknown");
  });

  it("passes the three real values straight through", () => {
    expect(itemVisibility({ visibility: "public" })).toBe("public");
    expect(itemVisibility({ visibility: "restricted" })).toBe("restricted");
    expect(itemVisibility({ visibility: "private" })).toBe("private");
  });
});

describe("itemGroupGrantsFromPermission (#608)", () => {
  it("decodes group: subjects into (group, role) at their deepest ladder tier", () => {
    const perm: import("./itemPermission").ItemPermission = {
      visibility: "restricted",
      read_meta: ["group:eng", "group:hr", "user:alice"],
      read_chat: ["group:eng", "group:hr"],
      read_content: ["group:eng"],
    };
    expect(itemGroupGrantsFromPermission(perm)).toEqual([
      { groupId: "eng", role: "reader" },
      { groupId: "hr", role: "in_workspace" },
    ]);
  });
});

describe("group grants resolve for the caller's groups (#608 gating)", () => {
  const ME = "erin";
  const OWN = "owner9";
  it("hasItemVerb grants a verb held via a group the caller belongs to", () => {
    const p = { visibility: "restricted" as const, read_content: ["group:eng"] };
    expect(hasItemVerb(p, ME, OWN, "read_content", false, ["eng"])).toBe(true);
    expect(hasItemVerb(p, ME, OWN, "read_content", false, ["hr"])).toBe(false); // not in eng
    expect(hasItemVerb(p, ME, OWN, "read_content", false, [])).toBe(false); // no groups known
  });
  it("canWriteItem honours a group write grant", () => {
    const p = { visibility: "restricted" as const, edit_content: ["group:eng"] };
    expect(canWriteItem(p, ME, OWN, false, ["eng"])).toBe(true);
    expect(canWriteItem(p, ME, OWN, false, [])).toBe(false);
  });
  it("canChangeItemPermission honours a group change_permission grant", () => {
    const p = { visibility: "restricted" as const, change_permission: ["group:eng"] };
    expect(canChangeItemPermission(p, ME, OWN, false, ["eng"])).toBe(true);
    expect(canChangeItemPermission(p, ME, OWN, false, [])).toBe(false);
  });
});
