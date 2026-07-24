import { describe, expect, it } from "vitest";

import type { Group } from "../api/groups";
import { groupCapabilities, groupRoleLabel } from "./groupRole";

const g = (over: Partial<Group> = {}): Group => ({
  resource_id: "g1",
  name: "Engineering",
  description: "",
  members: [],
  owner: "alice",
  maintainers: [],
  ...over,
});

describe("groupCapabilities — mirrors the backend gates", () => {
  it("the owner manages members AND the group (maintainers/transfer/delete)", () => {
    const c = groupCapabilities(g({ owner: "alice" }), "alice", false);
    expect(c).toEqual({ canManageMembers: true, canManageGroup: true });
  });

  it("a maintainer manages MEMBERS only, not the group", () => {
    const c = groupCapabilities(g({ owner: "alice", maintainers: ["dave"] }), "dave", false);
    expect(c).toEqual({ canManageMembers: true, canManageGroup: false });
  });

  it("a plain member manages nothing", () => {
    const c = groupCapabilities(g({ owner: "alice", members: ["bob"] }), "bob", false);
    expect(c).toEqual({ canManageMembers: false, canManageGroup: false });
  });

  it("a superuser manages everything on any group", () => {
    const c = groupCapabilities(g({ owner: "alice" }), "root", true);
    expect(c).toEqual({ canManageMembers: true, canManageGroup: true });
  });
});

describe("groupRoleLabel", () => {
  it("names the caller's relationship to the group", () => {
    expect(groupRoleLabel(g({ owner: "alice" }), "alice", false)).toBe("Owner");
    expect(groupRoleLabel(g({ maintainers: ["dave"] }), "dave", false)).toBe("Maintainer");
    expect(groupRoleLabel(g({ members: ["bob"] }), "bob", false)).toBe("Member");
    // a superuser who has no direct relationship is labelled by that power
    expect(groupRoleLabel(g({ owner: "alice" }), "root", true)).toBe("Admin");
  });
});
