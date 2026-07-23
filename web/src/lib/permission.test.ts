import { describe, expect, it } from "vitest";

import {
  type CollectionPermission,
  EVERYONE,
  canManageAccess,
  grantsFromPermission,
  groupGrantsFromPermission,
  permissionFromGrants,
  previewSubjects,
  roleForVerbs,
  subjectUser,
  userSubject,
} from "./permission";

const empty = (visibility: CollectionPermission["visibility"] = "restricted"): CollectionPermission => ({
  visibility,
  read_meta: [],
  write_meta: [],
  read_content: [],
  add_content: [],
  edit_content: [],
  read_chat: [],
  converse: [],
  execute: [],
  use_terminal: [],
  change_permission: [],
});

describe("subject encoding", () => {
  it("round-trips a user subject and rejects non-user subjects", () => {
    expect(userSubject("alice")).toBe("user:alice");
    expect(subjectUser("user:alice")).toBe("alice");
    expect(subjectUser("group:eng")).toBeNull();
    expect(subjectUser("all")).toBeNull();
  });
});

describe("roleForVerbs", () => {
  it("picks the highest tier the verbs satisfy", () => {
    expect(roleForVerbs(new Set(["read_meta", "read_content"]))).toBe("viewer");
    expect(roleForVerbs(new Set(["read_content", "add_content"]))).toBe("collaborator");
    expect(roleForVerbs(new Set(["read_content", "edit_content"]))).toBe("editor");
    expect(roleForVerbs(new Set())).toBeNull();
  });

  it("maps read_meta WITHOUT read_content to the discoverable tier", () => {
    // permission-disclosure: sees the collection exists (+ can request access),
    // but cannot read its content — the tier below Viewer.
    expect(roleForVerbs(new Set(["read_meta"]))).toBe("discoverable");
  });
});

describe("grantsFromPermission", () => {
  it("decodes user grants to (user, role), ignoring groups/all/owner", () => {
    const perm = empty();
    perm.read_meta = ["user:alice", "user:bob", "group:eng", "all", "user:bob-owner"];
    perm.read_content = ["user:alice", "user:bob", "group:eng"];
    perm.add_content = ["user:bob"];
    const grants = grantsFromPermission(perm, "bob-owner");
    expect(grants).toEqual([
      { userId: "alice", role: "viewer" },
      { userId: "bob", role: "collaborator" },
    ]);
  });
});

describe("permissionFromGrants", () => {
  it("writes each grant's role verbs and keeps the all wildcard + unmanaged verbs", () => {
    const original = empty("private");
    original.read_meta = ["group:eng", "all", "user:stale"];
    original.change_permission = ["user:admin"]; // unmanaged verb — must survive
    const next = permissionFromGrants(
      "restricted",
      [{ userId: "alice", role: "editor" }],
      original,
      [{ groupId: "eng", role: "viewer" }], // group grants are now round-tripped explicitly
    );
    expect(next.visibility).toBe("restricted");
    // stale user grant dropped; `all` kept; alice added; eng round-tripped as viewer
    expect(new Set(next.read_meta)).toEqual(new Set(["group:eng", "all", "user:alice"]));
    expect(next.edit_content).toEqual(["user:alice"]); // eng is viewer, not edit
    expect(new Set(next.add_content)).toEqual(new Set(["user:alice"]));
    // an unmanaged verb is preserved verbatim
    expect(next.change_permission).toEqual(["user:admin"]);
  });

  it("round-trips a dialog-shaped permission", () => {
    const start = permissionFromGrants(
      "restricted",
      [
        { userId: "alice", role: "viewer" },
        { userId: "carol", role: "editor" },
      ],
      empty(),
    );
    const grants = grantsFromPermission(start, "owner");
    expect(grants).toEqual([
      { userId: "alice", role: "viewer" },
      { userId: "carol", role: "editor" },
    ]);
  });
});

// #460 P6 — the advanced preview must reflect the SELECTED visibility, mirroring
// the backend authorize() semantics, not just echo the stored grant lists.
describe("previewSubjects — visibility semantics", () => {
  const withGrants = (): CollectionPermission => ({
    ...empty("restricted"),
    read_meta: ["user:alice"],
    read_content: ["user:alice", "user:bob"],
    change_permission: ["user:carol"],
  });

  it("public: every verb resolves to everyone, EXCEPT change_permission", () => {
    const p = withGrants();
    expect(previewSubjects("public", p, "read_meta")).toEqual([EVERYONE]);
    expect(previewSubjects("public", p, "converse")).toEqual([EVERYONE]);
    // change_permission is never opened up by visibility — grant-list only.
    expect(previewSubjects("public", p, "change_permission")).toEqual(["user:carol"]);
  });

  it("private: every managed verb resolves to nobody; change_permission still grant-list only", () => {
    const p = withGrants();
    expect(previewSubjects("private", p, "read_meta")).toEqual([]);
    expect(previewSubjects("private", p, "read_content")).toEqual([]);
    expect(previewSubjects("private", p, "change_permission")).toEqual(["user:carol"]);
  });

  it("restricted: echoes the per-verb grant list verbatim", () => {
    const p = withGrants();
    expect(previewSubjects("restricted", p, "read_content")).toEqual(["user:alice", "user:bob"]);
    expect(previewSubjects("restricted", p, "read_meta")).toEqual(["user:alice"]);
  });
});

describe("canManageAccess — the owner-or-superuser gate for management affordances", () => {
  it("lets the owner manage their own resource", () => {
    expect(canManageAccess("alice", "alice", false)).toBe(true);
  });

  it("lets a superuser manage a resource they do not own (authorize.py step 2)", () => {
    // The bug class this pins down: the control was `owner === me`, hiding the
    // entry point from admins even though the backend authorises them.
    expect(canManageAccess("alice", "root", true)).toBe(true);
  });

  it("hides the affordance from a plain non-owner", () => {
    expect(canManageAccess("alice", "bob", false)).toBe(false);
  });

  it("treats an UNKNOWN owner as not-mine, not as mine", () => {
    // `owner ?? me` gating treated "owner not loaded yet" as "my resource",
    // flashing management buttons at non-owners while data settles.
    expect(canManageAccess(undefined, "bob", false)).toBe(false);
    expect(canManageAccess(null, "bob", false)).toBe(false);
    // …but a superuser needs no owner fact at all.
    expect(canManageAccess(undefined, "root", true)).toBe(true);
  });
});

describe("group grants (#608)", () => {
  it("decodes group: subjects into (group, role) at their highest tier", () => {
    const perm = empty();
    perm.read_meta = ["group:eng", "group:hr", "user:alice", "all"];
    perm.read_content = ["group:eng", "group:hr"];
    perm.add_content = ["group:eng"];
    expect(groupGrantsFromPermission(perm)).toEqual([
      { groupId: "eng", role: "collaborator" },
      { groupId: "hr", role: "viewer" },
    ]);
  });

  it("rebuilds group grants on save AND keeps user grants + the all wildcard", () => {
    const original = empty("private");
    original.read_meta = ["all", "user:stalegroupless"];
    original.read_content = ["group:old"]; // an existing group grant to be replaced
    const next = permissionFromGrants(
      "restricted",
      [{ userId: "alice", role: "viewer" }],
      original,
      [{ groupId: "eng", role: "editor" }],
    );
    // `all` survives; the stale group grant is gone; eng gets the editor verbs;
    // alice keeps viewer.
    expect(new Set(next.read_meta)).toEqual(new Set(["all", "user:alice", "group:eng"]));
    expect(new Set(next.read_content)).toEqual(new Set(["user:alice", "group:eng"]));
    expect(next.edit_content).toEqual(["group:eng"]);
  });

  it("with no groupGrants passed, DROPS existing group grants (dialog must round-trip them)", () => {
    // Documents the contract: the dialog decodes group grants into state and
    // passes them back, or they're wiped — mirrors how user grants already work.
    const original = empty("restricted");
    original.read_meta = ["group:eng", "all"];
    const next = permissionFromGrants("restricted", [], original);
    expect(next.read_meta).toEqual(["all"]);
  });
});
