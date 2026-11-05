// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ItemSkillState } from "../api/types";
import { remindLater, skipVersion } from "../lib/skillUpdateReminders";
import { QueryWrap } from "../test/queryWrapper";
import { useSkillUpdateMark } from "./useSkillUpdateMark";

// plan-skill-hub-release-notes D1: the workspace's skills button carries a
// mark — how many of its skill hub copies have a new version this person has
// neither put off nor skipped — and judges nothing before it knows who they are.
const who = vi.hoisted(() => ({ state: { id: "alice", ready: true } }));
vi.mock("./useCurrentUser", () => ({
  useCurrentUser: () => who.state.id,
  useCurrentUserState: () => who.state,
}));

const behind = (name: string, entry: string, version: number): ItemSkillState => ({
  name,
  description: "",
  source: "workspace",
  default_on: true,
  is_copy: true,
  copy_of: "hub",
  hub_entry: entry,
  update_available: true,
  upstream: "live",
  pref: "follow",
  effective: true,
  updates: [{ version, kind: "publish", at: "2026-11-03T12:00:00Z", release_note: "", back_to: null }],
});
const current: ItemSkillState = { ...behind("fresh", "e9", 1), update_available: false, updates: [] };

function mark(skills: ItemSkillState[]) {
  const client = { getItemSkills: vi.fn(async () => skills) };
  return renderHook(() => useSkillUpdateMark("rca", "i1", client), { wrapper: QueryWrap });
}

describe("useSkillUpdateMark", () => {
  beforeEach(() => {
    localStorage.clear();
    who.state = { id: "alice", ready: true };
  });

  it("counts the copies with a new version this person has not answered", async () => {
    const { result } = mark([behind("a", "e1", 3), behind("b", "e2", 2), current]);
    await waitFor(() => expect(result.current).toBe(2));
  });

  it("a Later or a Skip takes it off the mark at once", async () => {
    const { result } = mark([behind("a", "e1", 3), behind("b", "e2", 2)]);
    await waitFor(() => expect(result.current).toBe(2));
    act(() => remindLater("alice", "e1", 3, Date.now()));
    await waitFor(() => expect(result.current).toBe(1));
    act(() => skipVersion("alice", "e2", 2));
    await waitFor(() => expect(result.current).toBe(0));
  });

  it("is zero until it knows who is looking (#997)", async () => {
    remindLater("alice", "e1", 3, Date.now());
    who.state = { id: "default-user", ready: false };
    const { result } = mark([behind("a", "e1", 3)]);
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current).toBe(0);
  });
});
