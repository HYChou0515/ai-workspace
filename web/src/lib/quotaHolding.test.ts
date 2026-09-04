/**
 * What a refusal offers to do about itself.
 *
 * Defaulting an item to its App's ceiling made hitting the limit ordinary
 * rather than exceptional, so the refusal is not an error path — it is the
 * feature's normal moment. Being told "you are full" and left to work out who
 * is holding it, on a page you have to know exists, turns that moment into a
 * dead end.
 */

import { describe, expect, it } from "vitest";

import { quotaHolding } from "./quotaHolding";

describe("quotaHolding", () => {
  it("lists what to close, with how much each one buys back", () => {
    const got = quotaHolding({
      error: "sandbox_quota_exceeded",
      dimension: "cpu",
      used: 4,
      limit: 4,
      holding: [
        { item_id: "i-1", title: "晶圓良率分析", cpu_cores: 2, memory_bytes: 0 },
        { item_id: "i-2", title: "客訴分類", cpu_cores: 2, memory_bytes: 0 },
      ],
    });

    expect(got.map((h) => h.itemId)).toEqual(["i-1", "i-2"]);
    expect(got[0].title).toBe("晶圓良率分析");
    expect(got[0].cpuCores).toBe(2);
  });

  it("is empty when the backend withheld the list", () => {
    // A collaborator hitting the OWNER's ceiling. They are owed the reason, not
    // the inventory — and the UI must render that as "no list", never as a
    // loading state or an error.
    const got = quotaHolding({
      error: "sandbox_quota_exceeded",
      dimension: "cpu",
      used: 4,
      limit: 4,
      holding: [],
    });

    expect(got).toEqual([]);
  });

  it("is empty for a refusal that is not about environments", () => {
    // Disk full has nothing to close — it has files to delete, in the item's own
    // file list. Offering a close button here would be the wrong remedy for the
    // right refusal.
    expect(quotaHolding({ error: "workspace_quota_exceeded", used: 10, quota: 10 })).toEqual([]);
  });

  it("survives a body from an older backend with no list at all", () => {
    expect(quotaHolding({ error: "sandbox_quota_exceeded", dimension: "cpu" })).toEqual([]);
    expect(quotaHolding(undefined)).toEqual([]);
  });

  it("drops a row with no id rather than rendering an unclosable button", () => {
    const got = quotaHolding({
      error: "sandbox_quota_exceeded",
      holding: [{ item_id: "", title: "?", cpu_cores: 1, memory_bytes: 0 }],
    });

    expect(got).toEqual([]);
  });
});
