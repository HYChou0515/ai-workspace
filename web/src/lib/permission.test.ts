import { describe, expect, it } from "vitest";

import {
  type CollectionPermission,
  grantsFromPermission,
  permissionFromGrants,
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
  it("writes each grant's role verbs and keeps group / all + unmanaged verbs", () => {
    const original = empty("private");
    original.read_meta = ["group:eng", "all", "user:stale"];
    original.change_permission = ["user:admin"]; // unmanaged verb — must survive
    const next = permissionFromGrants(
      "restricted",
      [{ userId: "alice", role: "editor" }],
      original,
    );
    expect(next.visibility).toBe("restricted");
    // stale user grant dropped; group + all kept; alice added
    expect(new Set(next.read_meta)).toEqual(new Set(["group:eng", "all", "user:alice"]));
    expect(next.edit_content).toEqual(["user:alice"]);
    expect(next.add_content).toEqual(["user:alice"]);
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
